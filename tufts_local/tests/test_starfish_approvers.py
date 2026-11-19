# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Project approvers and the Approver tags on their Starfish directories.

An approver is an active Manager who isn't the PI. ColdFront writes them to Starfish as
`Approver:<username>` tags on the project's directories (update_sf_approver_tags), and every night
reads the tags back (update_project_approvers_from_tags), so approvers set in Starfish reach
ColdFront.

The read-back used to find a directory's project by its name, and set roles from each directory in
turn. So a directory named after one project but belonging to another (rstore-cifs:StrleLab, owned
by another project than strlelab's) demoted the first project's approvers every night, and a
project's last directory decided its roles alone.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from coldfront.core.allocation.models import (
    Allocation,
    AllocationAttributeType,
    AllocationStatusChoice,
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

from tufts_local.tasks import update_project_approvers_from_tags

User = get_user_model()


def add_storage(project, resource_name, vol_path, status="Active"):
    """Give the project an allocation on the named resource, with vol_path as its sf_vol_path."""
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
    allocation.allocationattribute_set.create(
        allocation_attribute_type=AllocationAttributeType.objects.get_or_create(
            name="sf_vol_path", defaults={"attribute_type": AttributeType.objects.get_or_create(name="Text")[0]}
        )[0],
        value=vol_path,
    )
    return allocation


def add_member(project, username, role, status):
    user = User.objects.create(username=username)
    return ProjectUser.objects.create(project=project, user=user, role=role, status=status)


def make_project(title, roles, statuses, key=None):
    FieldOfScience.objects.get_or_create(pk=FieldOfScience.DEFAULT_PK, defaults={"description": "Other"})
    pi = User.objects.create(username=f"{title}_pi")
    project = Project.objects.create(
        title=title, pi=pi, status=ProjectStatusChoice.objects.get_or_create(name="Active")[0]
    )
    ProjectUser.objects.create(project=project, user=pi, role=roles["Manager"], status=statuses["Active"])
    if key:
        ProjectAttribute.objects.create(
            project=project,
            proj_attr_type=ProjectAttributeType.objects.get_or_create(
                name="Project Key",
                defaults={"attribute_type": ProjectAttributeValueType.objects.get_or_create(name="Text")[0]},
            )[0],
            value=key,
        )
    return project


def directory(vol_path, *approvers, other_tags=()):
    tags = [f"Approver:{a}" for a in approvers] + list(other_tags)
    return {"vol_path": vol_path, "tags_explicit": ",".join(tags)}


@pytest.fixture
def starfish():
    """Starfish's top-level directories: fill in starfish[volume] with directory() entries."""
    volumes = {}
    with (
        patch("tufts_local.tasks.get_starfish_volumes", side_effect=lambda client_key: list(volumes)),
        patch(
            "tufts_local.tasks.get_starfish_usage_data_by_volume",
            side_effect=lambda volume, client_key: volumes[volume],
        ),
    ):
        yield volumes


@pytest.fixture
def ad(db):
    """create_user, which looks a username up in AD: here it just makes the user."""
    with patch(
        "tufts_local.starfish_utils.create_user", side_effect=lambda username: User.objects.create(username=username)
    ) as create_user:
        yield create_user


def role_of(project, username):
    return ProjectUser.objects.get(project=project, user__username=username).role.name


# --- reading the tags back ------------------------------------------------------------------------


def test_a_directory_named_after_a_project_but_owned_by_another_leaves_its_roles_alone(roles, statuses, starfish):
    strlelab = make_project("strlelab", roles, statuses, key="strlelab")
    add_storage(strlelab, "Tier 1 HPC", "projects:strlelab")
    add_member(strlelab, "kbelay01", roles["Manager"], statuses["Active"])
    elsewhere = make_project("elsewhere", roles, statuses, key="elsewhere")
    add_storage(elsewhere, "Tier 1 RStore", "rstore-cifs:StrleLab")
    starfish["projects"] = [directory("projects:strlelab", "kbelay01")]
    starfish["rstore-cifs"] = [directory("rstore-cifs:StrleLab")]

    update_project_approvers_from_tags()

    assert role_of(strlelab, "kbelay01") == "Manager"


def test_a_user_tagged_on_any_of_the_projects_tier1_directories_is_an_approver(roles, statuses, starfish):
    project = make_project("newmanlab", roles, statuses)
    add_storage(project, "Tier 1 HPC", "projects:newmanlab")
    add_storage(project, "Tier 1 RStore", "rstore-cifs:NewmanLab")
    add_member(project, "approver01", roles["User"], statuses["Active"])
    add_member(project, "approver02", roles["User"], statuses["Active"])
    starfish["projects"] = [directory("projects:newmanlab", "approver01")]
    starfish["rstore-cifs"] = [directory("rstore-cifs:NewmanLab", "approver02")]

    update_project_approvers_from_tags()

    assert role_of(project, "approver01") == "Manager"
    assert role_of(project, "approver02") == "Manager"


def test_a_manager_tagged_on_none_of_the_projects_directories_becomes_a_user(roles, statuses, starfish):
    project = make_project("ekerlab", roles, statuses)
    add_storage(project, "Tier 1 HPC", "projects:ekerlab")
    add_storage(project, "Tier 1 RStore", "rstore-cifs:ekerlab")
    add_member(project, "former01", roles["Manager"], statuses["Active"])
    starfish["projects"] = [directory("projects:ekerlab")]
    starfish["rstore-cifs"] = [directory("rstore-cifs:ekerlab", other_tags=["Owner:ekerlab_pi"])]

    update_project_approvers_from_tags()

    assert role_of(project, "former01") == "User"
    assert role_of(project, "ekerlab_pi") == "Manager"


def test_vol_paths_match_whatever_their_case(roles, statuses, starfish):
    project = make_project("tytelllab", roles, statuses)
    add_storage(project, "Tier 1 RStore", "rstore-cifs:tytelllab")
    add_member(project, "approver01", roles["User"], statuses["Active"])
    starfish["rstore-cifs"] = [directory("rstore-cifs:TytellLab", "approver01")]

    update_project_approvers_from_tags()

    assert role_of(project, "approver01") == "Manager"


def test_a_directory_not_named_after_its_project_is_still_read(roles, statuses, starfish):
    project = make_project("suhlab", roles, statuses, key="suhlab")
    add_storage(project, "Tier 1 Other", "other:suhdata")
    add_member(project, "approver01", roles["User"], statuses["Active"])
    starfish["other"] = [directory("other:suhdata", "approver01")]

    update_project_approvers_from_tags()

    assert role_of(project, "approver01") == "Manager"


def test_with_tier1_storage_the_tier2_and_cold_directories_dont_count(roles, statuses, starfish):
    project = make_project("shenlab", roles, statuses)
    add_storage(project, "Tier 1 HPC", "projects:shenlab")
    add_storage(project, "Tier 2", "tier2:shenlab")
    add_storage(project, "Cold Storage", "cold:shenlab")
    add_member(project, "tier2only", roles["User"], statuses["Active"])
    add_member(project, "coldonly", roles["Manager"], statuses["Active"])
    starfish["projects"] = [directory("projects:shenlab")]
    starfish["tier2"] = [directory("tier2:shenlab", "tier2only")]
    starfish["cold"] = [directory("cold:shenlab", "coldonly")]

    update_project_approvers_from_tags()

    assert role_of(project, "tier2only") == "User"
    assert role_of(project, "coldonly") == "User"


def test_without_tier1_storage_the_projects_other_directories_count(roles, statuses, starfish):
    project = make_project("tier2lab", roles, statuses)
    add_storage(project, "Tier 2", "tier2:tier2lab")
    add_member(project, "approver01", roles["User"], statuses["Active"])
    starfish["tier2"] = [directory("tier2:tier2lab", "approver01")]

    update_project_approvers_from_tags()

    assert role_of(project, "approver01") == "Manager"


def test_an_inactive_allocations_directory_doesnt_count(roles, statuses, starfish):
    project = make_project("oldlab", roles, statuses)
    add_storage(project, "Tier 1 HPC", "projects:oldlab")
    add_storage(project, "Tier 1 RStore", "rstore-cifs:oldlab", status="Expired")
    add_member(project, "stale01", roles["User"], statuses["Active"])
    starfish["projects"] = [directory("projects:oldlab")]
    starfish["rstore-cifs"] = [directory("rstore-cifs:oldlab", "stale01")]

    update_project_approvers_from_tags()

    assert role_of(project, "stale01") == "User"


def test_a_project_with_none_of_its_directories_in_starfish_is_left_alone(roles, statuses, starfish):
    project = make_project("unindexed", roles, statuses)
    add_storage(project, "Tier 1 HPC", "projects:unindexed")
    add_member(project, "approver01", roles["Manager"], statuses["Active"])
    starfish["projects"] = []

    update_project_approvers_from_tags()

    assert role_of(project, "approver01") == "Manager"


def test_a_tagged_approver_who_isnt_a_member_is_added_as_one(roles, statuses, starfish, ad):
    project = make_project("growinglab", roles, statuses)
    add_storage(project, "Tier 1 HPC", "projects:growinglab")
    starfish["projects"] = [directory("projects:growinglab", "newcomer01")]

    update_project_approvers_from_tags()

    ad.assert_called_once_with("newcomer01")
    member = ProjectUser.objects.get(project=project, user__username="newcomer01")
    assert (member.role.name, member.status.name) == ("Manager", "Active")


def test_a_failure_on_one_project_doesnt_stop_the_others(roles, statuses, starfish, ad):
    broken = make_project("brokenlab", roles, statuses)
    add_storage(broken, "Tier 1 HPC", "projects:brokenlab")
    fine = make_project("finelab", roles, statuses)
    add_storage(fine, "Tier 1 HPC", "projects:finelab")
    add_member(fine, "approver01", roles["User"], statuses["Active"])
    starfish["projects"] = [directory("projects:brokenlab", "nosuchuser"), directory("projects:finelab", "approver01")]
    ad.side_effect = ValueError("no such user in AD")

    update_project_approvers_from_tags()

    assert role_of(fine, "approver01") == "Manager"
