# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The import_legacy_projects command: legacy projects' attributes and members, from a CSV.

AD, Grouper and Starfish are faked at their lowest layer (ADSearch, the Grouper client and the
Starfish API client), so the real caching in front of them is exercised.
"""

import csv
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import CommandError, call_command

from coldfront.core.allocation.models import (
    Allocation,
    AllocationAttributeType,
    AllocationStatusChoice,
    AllocationUser,
    AllocationUserStatusChoice,
    AttributeType,
)
from coldfront.core.field_of_science.models import FieldOfScience
from coldfront.core.project.models import AttributeType as ProjectAttributeValueType
from coldfront.core.project.models import (
    Project,
    ProjectAttribute,
    ProjectAttributeType,
    ProjectStatusChoice,
    ProjectUser,
)
from coldfront.core.resource.models import Resource, ResourceType

User = get_user_model()

CSV_HEADER = ["Name", "Project ID", "Project Key", "user_managed", "Group", "Users"]


@pytest.fixture(autouse=True)
def empty_cache():
    """Each test starts with nothing cached from another's lookups."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def attribute_types(db):
    text = ProjectAttributeValueType.objects.get_or_create(name="Text")[0]
    yes_no = ProjectAttributeValueType.objects.get_or_create(name="Yes/No")[0]
    return {
        "Project Key": ProjectAttributeType.objects.create(name="Project Key", attribute_type=text),
        "Group": ProjectAttributeType.objects.create(name="Group", attribute_type=text),
        "users_managed": ProjectAttributeType.objects.create(name="users_managed", attribute_type=yes_no),
    }


@pytest.fixture
def allocation_statuses(db):
    for name in ("Active", "Removed"):
        AllocationUserStatusChoice.objects.get_or_create(name=name)


def make_legacy_project(title, key, roles, statuses, attribute_types, pi="pi01", vol_path=None):
    """An active project with its PI as a Manager member, its Project Key, and an Active Tier 1 allocation
    with vol_path (default rstore-cifs:<title>) as its sf_vol_path."""
    FieldOfScience.objects.get_or_create(pk=FieldOfScience.DEFAULT_PK, defaults={"description": "Other"})
    pi_user = User.objects.get_or_create(username=pi)[0]
    project = Project.objects.create(
        title=title, pi=pi_user, status=ProjectStatusChoice.objects.get_or_create(name="Active")[0]
    )
    ProjectUser.objects.create(project=project, user=pi_user, role=roles["Manager"], status=statuses["Active"])
    ProjectAttribute.objects.create(project=project, proj_attr_type=attribute_types["Project Key"], value=key)
    add_allocation(project, "Tier 1 Storage", vol_path or f"rstore-cifs:{title}")
    return project


def add_allocation(project, resource_name, vol_path=None, status="Active"):
    resource, _ = Resource.objects.get_or_create(
        name=resource_name,
        defaults={
            "description": resource_name,
            "resource_type": ResourceType.objects.get_or_create(name="Storage", defaults={"description": "x"})[0],
        },
    )
    allocation = Allocation.objects.create(
        project=project, status=AllocationStatusChoice.objects.get_or_create(name=status)[0], justification="why"
    )
    allocation.resources.add(resource)
    if vol_path:
        allocation.allocationattribute_set.create(
            allocation_attribute_type=AllocationAttributeType.objects.get_or_create(
                name="sf_vol_path", defaults={"attribute_type": AttributeType.objects.get_or_create(name="Text")[0]}
            )[0],
            value=vol_path,
        )
    return allocation


@pytest.fixture
def legacy_project(db, roles, statuses, attribute_types, allocation_statuses):
    """TziporiLab, project key tziporilab, PI stzipo01."""
    return make_legacy_project("TziporiLab", "tziporilab", roles, statuses, attribute_types, pi="stzipo01")


class FakeADSearch:
    """ADSearch over a dict of username -> entry; counts the searches made."""

    entries = {}
    searches = []

    def __init__(self, user_search_string, search_by):
        pass

    def search_a_user(self, user_search_string=None, search_by="all_fields", size_limit=None):
        FakeADSearch.searches.append(user_search_string)
        entry = FakeADSearch.entries.get(user_search_string)
        return [entry] if entry else []


@pytest.fixture
def ad():
    """AD: add usernames with ad.add(...). Every search is recorded in ad.searches."""
    FakeADSearch.entries = {}
    FakeADSearch.searches = []

    def add(*usernames):
        for username in usernames:
            FakeADSearch.entries[username] = {
                "username": username,
                "first_name": f"First {username}",
                "last_name": f"Last {username}",
                "email": f"{username}@example.edu",
            }

    FakeADSearch.add = staticmethod(add)
    with patch("tufts_local.legacy_import.ADSearch", FakeADSearch):
        yield FakeADSearch


class FakeGrouperClient:
    """Grouper's groups as a dict of name -> list of usernames; records every call. Adding a username in
    refused fails for that user; adding while unreachable is set raises it."""

    def __init__(self):
        self.groups = {}
        self.calls = []
        self.refused = set()
        self.unreachable = None

    def group_exists(self, group_name):
        self.calls.append(("group_exists", group_name))
        return group_name in self.groups

    def get_group_members(self, group_name):
        self.calls.append(("get_group_members", group_name))
        return {f"id-{u}": u for u in self.groups.get(group_name, [])}

    def add_members_to_group(self, group_name, member_usernames):
        self.calls.append(("add_members_to_group", group_name, sorted(member_usernames)))
        if self.unreachable:
            raise self.unreachable
        outcomes = {}
        for username in member_usernames:
            outcomes[username] = username not in self.refused
            if outcomes[username] and username not in self.groups[group_name]:
                self.groups[group_name].append(username)
        return outcomes

    def additions(self):
        return [call[1:] for call in self.calls if call[0] == "add_members_to_group"]


@pytest.fixture
def grouper():
    client = FakeGrouperClient()
    with patch("tufts_local.legacy_import.get_grouper_client", return_value=client):
        yield client


class FakeStarfishClient:
    """Starfish's top-level directories by volume; records every volume queried."""

    def __init__(self):
        self.volumes = {}
        self.queried = []

    def request_subfolder_query(self, volume):
        self.queried.append(volume)
        return [
            {"logical_size": 0, "sync": None, "username": "", "groupname": "", **entry}
            for entry in self.volumes.get(volume, [])
        ]


def sf_directory(vol_path, approvers=(), owner=None):
    tags = [f"Approver:{a}" for a in approvers] + ([f"Owner:{owner}"] if owner else [])
    return {"vol_path": vol_path, "tags_explicit": ",".join(tags)}


@pytest.fixture
def starfish():
    """Starfish: fill in starfish.volumes[volume] with sf_directory() entries."""
    client = FakeStarfishClient()
    with patch("tufts_local.starfish_utils.get_starfish_client", return_value=client):
        yield client


def write_rows(tmp_path, *rows):
    """A CSV like the RCRM export, with a byte-order mark, holding the rows (lists in CSV_HEADER's order)."""
    path = tmp_path / "import.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)
    return path


def tzipori_row(project, users="aryan06,kmurph22", group="tziporilab", name="TziporiLab", key="tziporilab"):
    return [name, str(project.pk), key, "Yes", group, users]


def run_legacy_import(path, *args):
    """Run the command; its output, stdout then stderr."""
    out, err = StringIO(), StringIO()
    call_command("import_legacy_projects", str(path), *args, stdout=out, stderr=err)
    return out.getvalue() + err.getvalue()


def attribute_values(project, name):
    return sorted(project.projectattribute_set.filter(proj_attr_type__name=name).values_list("value", flat=True))


def membership(project, username):
    pu = ProjectUser.objects.filter(project=project, user__username=username).first()
    return (pu.role.name, pu.status.name) if pu else None


def allocation_usernames(allocation, status="Active"):
    return sorted(
        AllocationUser.objects.filter(allocation=allocation, status__name=status).values_list(
            "user__username", flat=True
        )
    )


@pytest.fixture
def ready(legacy_project, ad, grouper, starfish):
    """TziporiLab ready to import: the CSV's users aryan06 and kmurph22 are in AD, and kmurph22 is tagged as
    an approver on its directory. Its group tziporilab holds only the PI, as the real one does."""
    ad.add("aryan06", "kmurph22")
    grouper.groups["tziporilab"] = ["stzipo01"]
    starfish.volumes["rstore-cifs"] = [sf_directory("rstore-cifs:TziporiLab", approvers=["kmurph22"])]
    return legacy_project


# --- reading the CSV -------------------------------------------------------------------------------


def test_the_csv_is_read_with_its_byte_order_mark_and_users_split_and_lowercased(tmp_path):
    from tufts_local.legacy_import import read_legacy_csv

    path = write_rows(tmp_path, ["CebeLab", "1343", "cebelab", "Yes", "cebelab", "PCebe01, ab02 ,"])
    [row] = read_legacy_csv(path)
    assert (row.line, row.name, row.project_id, row.project_key) == (2, "CebeLab", "1343", "cebelab")
    assert (row.user_managed, row.group, row.users) == ("Yes", "cebelab", ["pcebe01", "ab02"])


# --- making the changes ----------------------------------------------------------------------------


def test_the_attributes_are_set(tmp_path, ready):
    run_legacy_import(write_rows(tmp_path, tzipori_row(ready)))
    assert attribute_values(ready, "users_managed") == ["Yes"]
    assert attribute_values(ready, "Group") == ["tziporilab"]


def test_a_different_group_is_replaced_and_the_old_one_reported(tmp_path, ready, attribute_types):
    ProjectAttribute.objects.create(project=ready, proj_attr_type=attribute_types["Group"], value="old_group")
    output = run_legacy_import(write_rows(tmp_path, tzipori_row(ready)))
    assert attribute_values(ready, "Group") == ["tziporilab"]
    assert "old_group" in output


def test_the_csvs_users_are_added_as_managers_if_tagged_as_approvers_and_users_if_not(tmp_path, ready):
    """Though neither is in the Grouper group."""
    run_legacy_import(write_rows(tmp_path, tzipori_row(ready)))
    assert membership(ready, "kmurph22") == ("Manager", "Active")
    assert membership(ready, "aryan06") == ("User", "Active")


def test_a_new_users_details_come_from_ad(tmp_path, ready):
    run_legacy_import(write_rows(tmp_path, tzipori_row(ready)))
    user = User.objects.get(username="aryan06")
    assert (user.first_name, user.last_name, user.email) == ("First aryan06", "Last aryan06", "aryan06@example.edu")


def test_the_pi_is_left_alone_even_untagged(tmp_path, ready, ad):
    ad.add("stzipo01")
    run_legacy_import(write_rows(tmp_path, tzipori_row(ready, users="aryan06,kmurph22,stzipo01")))
    assert membership(ready, "stzipo01") == ("Manager", "Active")
    assert ready.pi.username == "stzipo01"


def test_an_existing_members_role_and_status_are_set(tmp_path, ready, roles, statuses):
    ProjectUser.objects.create(
        project=ready, user=User.objects.create(username="kmurph22"), role=roles["User"], status=statuses["Removed"]
    )
    run_legacy_import(write_rows(tmp_path, tzipori_row(ready)))
    assert membership(ready, "kmurph22") == ("Manager", "Active")


def test_a_member_not_in_the_csv_is_reported_and_left_alone(tmp_path, ready, roles, statuses):
    ProjectUser.objects.create(
        project=ready, user=User.objects.create(username="other01"), role=roles["User"], status=statuses["Active"]
    )
    output = run_legacy_import(write_rows(tmp_path, tzipori_row(ready)))
    assert membership(ready, "other01") == ("User", "Active")
    assert "other01" in output


def test_csv_users_not_in_the_group_are_added_to_it(tmp_path, ready, grouper):
    run_legacy_import(write_rows(tmp_path, tzipori_row(ready)))
    assert grouper.additions() == [("tziporilab", ["aryan06", "kmurph22"])]
    assert sorted(grouper.groups["tziporilab"]) == ["aryan06", "kmurph22", "stzipo01"]


def test_csv_users_already_in_the_group_arent_added_again(tmp_path, ready, grouper):
    grouper.groups["tziporilab"].append("aryan06")
    run_legacy_import(write_rows(tmp_path, tzipori_row(ready)))
    assert grouper.additions() == [("tziporilab", ["kmurph22"])]


def test_csv_users_not_in_ad_arent_added_to_the_group(tmp_path, ready, grouper):
    run_legacy_import(write_rows(tmp_path, tzipori_row(ready, users="aryan06,kmurph22,gone01")))
    assert grouper.additions() == [("tziporilab", ["aryan06", "kmurph22"])]


def test_no_one_is_added_to_a_group_already_holding_the_csvs_users(tmp_path, ready, grouper):
    grouper.groups["tziporilab"] += ["aryan06", "kmurph22"]
    run_legacy_import(write_rows(tmp_path, tzipori_row(ready)))
    assert grouper.additions() == []


def test_a_refused_group_addition_is_reported_and_the_projects_changes_stand(tmp_path, ready, grouper):
    grouper.refused = {"aryan06"}
    with pytest.raises(CommandError, match="Grouper"):
        run_legacy_import(write_rows(tmp_path, tzipori_row(ready)))
    assert membership(ready, "aryan06") == ("User", "Active")
    assert sorted(grouper.groups["tziporilab"]) == ["kmurph22", "stzipo01"]


def test_an_unreachable_grouper_is_reported_and_the_projects_changes_stand(tmp_path, ready, grouper):
    grouper.unreachable = OSError("connection refused")
    with pytest.raises(CommandError, match="Grouper"):
        run_legacy_import(write_rows(tmp_path, tzipori_row(ready)))
    assert membership(ready, "aryan06") == ("User", "Active")


def test_a_rerun_retries_only_the_refused_group_additions(tmp_path, ready, grouper):
    grouper.refused = {"aryan06"}
    path = write_rows(tmp_path, tzipori_row(ready))
    with pytest.raises(CommandError):
        run_legacy_import(path)
    grouper.refused = set()
    run_legacy_import(path)
    assert grouper.additions()[-1] == ("tziporilab", ["aryan06"])
    assert sorted(grouper.groups["tziporilab"]) == ["aryan06", "kmurph22", "stzipo01"]


def test_csv_users_not_in_the_group_are_noted(tmp_path, ready):
    output = run_legacy_import(write_rows(tmp_path, tzipori_row(ready)))
    assert "in the CSV's Users but not the Grouper group" in output
    assert "aryan06, kmurph22" in output


def test_a_csv_user_not_in_ad_is_noted_and_skipped(tmp_path, ready):
    output = run_legacy_import(write_rows(tmp_path, tzipori_row(ready, users="aryan06,kmurph22,gone01")))
    assert "in the CSV's Users but not in AD (not added): gone01" in output
    assert "error:" not in output
    assert membership(ready, "gone01") is None
    assert membership(ready, "aryan06") == ("User", "Active")


def test_group_members_who_arent_project_users_are_noted_not_added(tmp_path, ready, grouper, ad):
    ad.add("grouponly01")
    grouper.groups["tziporilab"].append("grouponly01")
    output = run_legacy_import(write_rows(tmp_path, tzipori_row(ready)))
    assert "in the Grouper group but not project or allocation users (not added): grouponly01" in output
    assert membership(ready, "grouponly01") is None


def test_group_members_who_are_or_become_project_users_arent_noted(tmp_path, ready, grouper):
    """stzipo01 is the PI, a member already; aryan06 is added from the CSV."""
    grouper.groups["tziporilab"].append("aryan06")
    output = run_legacy_import(write_rows(tmp_path, tzipori_row(ready)))
    assert "in the Grouper group but not" not in output


def test_an_approver_tagged_but_not_in_the_csv_is_reported_not_added(tmp_path, ready, starfish):
    starfish.volumes["rstore-cifs"] = [sf_directory("rstore-cifs:TziporiLab", approvers=["kmurph22", "tagonly01"])]
    output = run_legacy_import(write_rows(tmp_path, tzipori_row(ready)))
    assert "tagonly01" in output
    assert membership(ready, "tagonly01") is None


def test_an_owner_tag_naming_someone_other_than_the_pi_is_reported(tmp_path, ready, starfish):
    starfish.volumes["rstore-cifs"] = [
        sf_directory("rstore-cifs:TziporiLab", approvers=["kmurph22"], owner="someoneelse01")
    ]
    output = run_legacy_import(write_rows(tmp_path, tzipori_row(ready)))
    assert "someoneelse01" in output
    assert ready.pi.username == "stzipo01"


def test_active_members_become_users_of_each_active_allocation(tmp_path, ready):
    other = add_allocation(ready, "Tier 2 Storage")
    inactive = add_allocation(ready, "Tier 2 Storage", status="Expired")
    run_legacy_import(write_rows(tmp_path, tzipori_row(ready)))
    tier1 = ready.allocation_set.get(resources__name="Tier 1 Storage")
    assert allocation_usernames(tier1) == ["aryan06", "kmurph22", "stzipo01"]
    assert allocation_usernames(other) == ["aryan06", "kmurph22", "stzipo01"]
    assert allocation_usernames(inactive) == []


def test_a_removed_allocation_user_is_made_active(tmp_path, ready):
    tier1 = ready.allocation_set.get()
    tier1.allocationuser_set.create(
        user=User.objects.create(username="aryan06"), status=AllocationUserStatusChoice.objects.get(name="Removed")
    )
    run_legacy_import(write_rows(tmp_path, tzipori_row(ready)))
    assert "aryan06" in allocation_usernames(tier1)


def test_an_allocation_user_who_isnt_a_member_is_reported_and_left_alone(tmp_path, ready):
    tier1 = ready.allocation_set.get()
    tier1.allocationuser_set.create(
        user=User.objects.create(username="stray01"), status=AllocationUserStatusChoice.objects.get(name="Active")
    )
    output = run_legacy_import(write_rows(tmp_path, tzipori_row(ready)))
    assert "stray01" in allocation_usernames(tier1)
    assert "stray01" in output


def test_running_it_again_changes_nothing_more(tmp_path, ready):
    path = write_rows(tmp_path, tzipori_row(ready))
    run_legacy_import(path)
    counts = (ProjectUser.objects.count(), AllocationUser.objects.count(), ProjectAttribute.objects.count())
    output = run_legacy_import(path)
    assert (ProjectUser.objects.count(), AllocationUser.objects.count(), ProjectAttribute.objects.count()) == counts
    assert "change:" not in output


def test_the_saves_dont_queue_an_approver_tag_write_back(tmp_path, ready, django_capture_on_commit_callbacks):
    with (
        patch("tufts_local.signals.SF_OWNER_TAG_PERSIST", True),
        patch("tufts_local.signals._queue_approver_tags") as queue,
        django_capture_on_commit_callbacks(execute=True),
    ):
        run_legacy_import(write_rows(tmp_path, tzipori_row(ready)))
    queue.assert_not_called()


def test_the_changes_are_made_as_the_acting_user(tmp_path, ready):
    """task_context's acting_as() is what makes simple_history record them as the history rows' user."""
    with patch("tufts_local.management.commands.import_legacy_projects.acting_as") as acting_as:
        run_legacy_import(write_rows(tmp_path, tzipori_row(ready)), "--acting-user", "admin01")
    acting_as.assert_called_once_with("admin01")


# --- validation ------------------------------------------------------------------------------------


def test_a_dry_run_changes_nothing_but_shows_the_changes(tmp_path, ready):
    output = run_legacy_import(write_rows(tmp_path, tzipori_row(ready)), "--dry-run")
    assert attribute_values(ready, "users_managed") == []
    assert attribute_values(ready, "Group") == []
    assert membership(ready, "aryan06") is None
    assert AllocationUser.objects.count() == 0
    assert "aryan06" in output and "kmurph22" in output
    assert "Dry run" in output


def test_a_dry_run_adds_no_one_to_the_group_but_shows_who_it_would(tmp_path, ready, grouper):
    output = run_legacy_import(write_rows(tmp_path, tzipori_row(ready)), "--dry-run")
    assert grouper.additions() == []
    assert grouper.groups["tziporilab"] == ["stzipo01"]
    assert "change: add aryan06, kmurph22 to Grouper group 'tziporilab'" in output


@pytest.mark.parametrize(
    "mismatch",
    [
        {"name": "SomeOtherLab"},
        {"key": "someotherkey"},
    ],
)
def test_a_name_or_key_that_doesnt_match_the_project_is_an_error(tmp_path, ready, mismatch):
    output = run_legacy_import(write_rows(tmp_path, tzipori_row(ready, **mismatch)), "--dry-run")
    assert "error:" in output
    assert "0 invalid" not in output


def test_a_name_and_key_differing_only_in_case_match(tmp_path, ready):
    output = run_legacy_import(
        write_rows(tmp_path, tzipori_row(ready, name="tziporilab", key="TziporiLab")), "--dry-run"
    )
    assert "error:" not in output


def test_a_missing_project_is_an_error(tmp_path, ready):
    output = run_legacy_import(write_rows(tmp_path, ["Nope", "999999", "nope", "Yes", "nope", ""]), "--dry-run")
    assert "999999" in output and "error:" in output


def test_an_archived_project_is_an_error(tmp_path, ready):
    ready.status = ProjectStatusChoice.objects.get_or_create(name="Archived")[0]
    ready.save()
    output = run_legacy_import(write_rows(tmp_path, tzipori_row(ready)), "--dry-run")
    assert "error:" in output


def test_a_group_not_in_grouper_is_an_error(tmp_path, ready, grouper):
    del grouper.groups["tziporilab"]
    output = run_legacy_import(write_rows(tmp_path, tzipori_row(ready)), "--dry-run")
    assert "error:" in output and "Grouper" in output


def test_a_group_another_project_has_is_an_error(tmp_path, ready, roles, statuses, attribute_types):
    other = make_legacy_project("OtherLab", "otherlab", roles, statuses, attribute_types, pi="pi02")
    ProjectAttribute.objects.create(project=other, proj_attr_type=attribute_types["Group"], value="tziporilab")
    output = run_legacy_import(write_rows(tmp_path, tzipori_row(ready)), "--dry-run")
    assert "error:" in output and str(other.pk) in output


def test_a_project_with_no_directory_in_starfish_is_an_error(tmp_path, ready, starfish):
    starfish.volumes["rstore-cifs"] = []
    output = run_legacy_import(write_rows(tmp_path, tzipori_row(ready)), "--dry-run")
    assert "error:" in output and "Starfish" in output


def test_csv_users_are_checked_in_ad_even_when_starfish_has_no_directory(tmp_path, ready, starfish):
    """So one dry run shows every problem with a project, not the first."""
    starfish.volumes["rstore-cifs"] = []
    output = run_legacy_import(write_rows(tmp_path, tzipori_row(ready, users="aryan06,gone01")), "--dry-run")
    assert "Starfish" in output and "not in AD (not added): gone01" in output


def test_a_missing_users_managed_attribute_type_is_an_error(tmp_path, ready, attribute_types):
    attribute_types["users_managed"].delete()
    output = run_legacy_import(write_rows(tmp_path, tzipori_row(ready)), "--dry-run")
    assert "error:" in output and "users_managed" in output


def test_the_real_run_changes_nothing_if_any_row_is_invalid(tmp_path, ready, roles, statuses, attribute_types):
    broken = make_legacy_project("BrokenLab", "brokenlab", roles, statuses, attribute_types, pi="pi02")
    path = write_rows(tmp_path, tzipori_row(ready), ["BrokenLab", str(broken.pk), "brokenlab", "Yes", "nogroup", ""])
    with pytest.raises(CommandError):
        run_legacy_import(path)
    assert attribute_values(ready, "Group") == []
    assert membership(ready, "aryan06") is None


def test_skip_invalid_changes_the_valid_rows(tmp_path, ready, roles, statuses, attribute_types):
    broken = make_legacy_project("BrokenLab", "brokenlab", roles, statuses, attribute_types, pi="pi02")
    path = write_rows(tmp_path, tzipori_row(ready), ["BrokenLab", str(broken.pk), "brokenlab", "Yes", "nogroup", ""])
    run_legacy_import(path, "--skip-invalid")
    assert attribute_values(ready, "Group") == ["tziporilab"]
    assert attribute_values(broken, "Group") == []


# --- caching ---------------------------------------------------------------------------------------


def test_a_dry_run_then_the_real_run_ask_each_system_once(tmp_path, ready, ad, grouper, starfish):
    path = write_rows(tmp_path, tzipori_row(ready))
    run_legacy_import(path, "--dry-run")
    run_legacy_import(path)
    assert sorted(ad.searches) == ["aryan06", "kmurph22"]
    assert grouper.calls.count(("get_group_members", "tziporilab")) == 1
    assert starfish.queried.count("rstore-cifs") == 1


def test_a_user_missing_from_ad_is_asked_about_again(tmp_path, ready, ad):
    path = write_rows(tmp_path, tzipori_row(ready, users="aryan06,kmurph22,gone01"))
    run_legacy_import(path, "--dry-run")
    ad.add("gone01")
    output = run_legacy_import(path, "--dry-run")
    assert "not in AD" not in output
    assert "add gone01 as User" in output


def test_a_group_missing_from_grouper_is_asked_about_again(tmp_path, ready, grouper):
    members = grouper.groups.pop("tziporilab")
    path = write_rows(tmp_path, tzipori_row(ready))
    run_legacy_import(path, "--dry-run")
    grouper.groups["tziporilab"] = members
    output = run_legacy_import(path, "--dry-run")
    assert "error:" not in output
