# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Bring legacy projects' settings and members into ColdFront from a spreadsheet.

Each row names a project three ways (Name, Project ID, Project Key), all of which must agree, and
gives the values to set: its users_managed and Group project attributes, from the user_managed and
Group columns. The group must be in Grouper. The users in the Users column who are in AD become
Active members of the project, whether or not they're in the group, with the role the Approver tags
on the project's Starfish directories give them: tagged users are Managers, others Users. The PI is
left alone. Every active member is then made an active user of each of the project's Active
allocations, as ColdFront's Add Users does, so its allocation users match its project users. Those
users who aren't in the Grouper group are then added to it, as Add Users would; not in a dry run.
Group members who still aren't project users are noted, not added.

AD, Grouper and Starfish answers are cached in Django's cache (Redis on the hosts) for
CACHE_TIMEOUT, so a dry run and the real run that follows it ask each system about each name once.
Only answers that found something are cached: a user missing from AD, or a group missing from
Grouper, is asked about again next run, so a fix made after a dry run is seen.
"""

import csv
import functools
import hashlib
import os
from dataclasses import dataclass, field

from coldfront_utils import ttl_cache
from coldfront_utils.util.ad_search import ADSearch
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction

from coldfront.core.allocation.models import AllocationUserStatusChoice
from coldfront.core.project.models import (
    Project,
    ProjectAttribute,
    ProjectAttributeType,
    ProjectUser,
    ProjectUserRoleChoice,
    ProjectUserStatusChoice,
)

from tufts_local.starfish_utils import (
    get_starfish_usage_data_by_volume,
    get_starfish_volumes,
    parse_tags,
    roles_from_starfish,
)

# long enough to cover a dry run, fixing what it found, and the real run
CACHE_TIMEOUT = 6 * 60 * 60

USERS_MANAGED_ATTRIBUTE = "users_managed"
GROUP_ATTRIBUTE = "Group"
PROJECT_KEY_ATTRIBUTE = "Project Key"


@dataclass
class LegacyRow:
    """One row of the spreadsheet, with its line number for messages."""

    line: int
    name: str
    project_id: str
    project_key: str
    user_managed: str
    group: str
    users: list[str]


@dataclass
class MemberChange:
    """A Grouper member to add to the project, or whose membership to change."""

    username: str
    role: str
    ad_entry: dict
    description: str


@dataclass
class ProjectPlan:
    """What validating a row found, and what applying it would change.

    errors stop the project from being changed; notes are for a person to read but change nothing.
    """

    row: LegacyRow
    project: object = None
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    attribute_changes: dict[str, tuple[list[str], str]] = field(default_factory=dict)
    member_changes: list[MemberChange] = field(default_factory=list)
    # (allocation, username): an active member to make an active user of one of the project's Active allocations
    allocation_additions: list[tuple[object, str]] = field(default_factory=list)
    # the CSV's users in AD to add to the Grouper group, which they aren't in
    grouper_additions: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


def read_legacy_csv(path) -> list[LegacyRow]:
    """Read the spreadsheet's rows. Users is a comma-separated list; usernames are lowercased."""
    rows = []
    # utf-8-sig: the RCRM export starts with a byte-order mark, which would otherwise be part of "Name"
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for record in reader:
            rows.append(
                LegacyRow(
                    line=reader.line_num,
                    name=record["Name"].strip(),
                    project_id=record["Project ID"].strip(),
                    project_key=record["Project Key"].strip(),
                    user_managed=record["user_managed"].strip(),
                    group=record["Group"].strip(),
                    users=[u.strip().lower() for u in record["Users"].split(",") if u.strip()],
                )
            )
    return rows


@ttl_cache(timeout=CACHE_TIMEOUT)
def find_ad_user(username: str) -> dict | None:
    """The AD entry for the username (username, first_name, last_name, email), or None if there's none."""
    result = ADSearch(username, "username_only").search_a_user(user_search_string=username, search_by="username_only")
    return dict(result[0]) if len(result) == 1 else None


@functools.cache
def get_grouper_client():
    """A Grouper client configured as the user_management plugin's is, from the GROUPER_* variables."""
    # imported here: it's installed with the user_management plugin, not a dependency of this one
    from grouper_client import GrouperClient

    return GrouperClient(
        os.getenv("GROUPER_API_URL"),
        os.getenv("GROUPER_ENTITY_ID"),
        os.getenv("GROUPER_KEY_PATH"),
        os.getenv("GROUPER_GROUP_STEM"),
    )


def grouper_group_members(group: str) -> list[str] | None:
    """The group's members' usernames, lowercased and sorted, or None if Grouper has no such group.

    Cached for CACHE_TIMEOUT like the other lookups, but not with ttl_cache, so that adding users to the
    group can update the cached members (remember_group_members).
    """
    members = cache.get(group_members_cache_key(group))
    if members is not None:
        return members
    client = get_grouper_client()
    if not client.group_exists(group):
        return None
    members = sorted(username.lower() for username in client.get_group_members(group).values())
    remember_group_members(group, members)
    return members


def remember_group_members(group: str, members: list[str]) -> None:
    cache.set(group_members_cache_key(group), sorted(members), timeout=CACHE_TIMEOUT)


def group_members_cache_key(group: str) -> str:
    # hashed, as ttl_cache does, so the key holds nothing memcached would refuse
    return f"tufts_local.legacy_import.grouper_group_members:{hashlib.sha256(group.encode()).hexdigest()}"


def starfish_tags_by_vol_path() -> dict[str, dict[str, set]]:
    """Every top-level Starfish directory's tags, keyed by its vol_path lowercased.

    Read a volume at a time through get_starfish_usage_data_by_volume, which caches each volume.
    """
    tags = {}
    for volume in get_starfish_volumes("starfish"):
        for entry in get_starfish_usage_data_by_volume(volume, "starfish"):
            tags[entry["vol_path"].lower()] = parse_tags((entry.get("tags_explicit") or "").split(","))
    return tags


def plan_project(row: LegacyRow, tags_by_vol_path: dict, vol_paths_by_project_id: dict) -> ProjectPlan:
    """Validate the row against ColdFront, AD, Grouper and Starfish, and work out what it would change.

    vol_paths_by_project_id gives the directories whose Approver tags count for each project, as the
    nightly read-back of the tags (update_project_approvers_from_tags) takes them, so the two agree.
    """
    plan = ProjectPlan(row)
    project = find_project(plan)
    if project is None:
        return plan
    plan.project = project
    plan_attributes(plan)

    try:
        members = grouper_group_members(row.group)
    except Exception as e:
        plan.errors.append(f"couldn't look group {row.group!r} up in Grouper: {e!r}")
        return plan
    if members is None:
        plan.errors.append(f"group {row.group!r} isn't in Grouper")
        return plan
    note_usernames(plan, "in the CSV's Users but not the Grouper group", set(row.users) - set(members))
    ad_entries = look_up_users_in_ad(plan, row.users)
    plan.grouper_additions = sorted(set(ad_entries) - set(members))

    vol_paths = vol_paths_by_project_id.get(project.pk, set())
    tagged = [tags_by_vol_path[vp.lower()] for vp in vol_paths if vp.lower() in tags_by_vol_path]
    if not tagged:
        where = f"none of its directories, {sorted(vol_paths)}, is" if vol_paths else "it has no Active allocation"
        plan.errors.append(f"can't read roles from Starfish: {where} in Starfish")
        return plan
    approvers = {a.lower() for tags in tagged for a in tags.get("Approver", set())}
    owners = {o.lower() for tags in tagged for o in tags.get("Owner", set())}
    pi = project.pi.username.lower()
    if owners and owners != {pi}:
        plan.notes.append(f"Starfish's Owner tags name {', '.join(sorted(owners))}, but the PI is {pi} (left as is)")
    note_usernames(
        plan,
        "tagged as approvers in Starfish but not in the CSV's Users (not added)",
        approvers - set(row.users) - {pi},
    )

    plan_members(plan, ad_entries, approvers)
    if plan.is_valid:
        plan_allocation_users(plan)
    note_usernames(
        plan,
        "in the Grouper group but not project or allocation users (not added)",
        set(members) - active_members_after(plan),
    )
    return plan


def find_project(plan: ProjectPlan):
    """The row's project, if its Name, Project ID and Project Key all name the same Active project."""
    row = plan.row
    try:
        project = Project.objects.select_related("pi", "status").filter(pk=int(row.project_id)).first()
    except ValueError:
        project = None
    if project is None:
        plan.errors.append(f"there's no project {row.project_id}")
        return None
    if project.title.lower() != row.name.lower():
        plan.errors.append(f"Name {row.name!r} doesn't match project {project.pk}'s title, {project.title!r}")
    keys = attribute_values(project, PROJECT_KEY_ATTRIBUTE)
    if row.project_key.lower() not in {key.lower() for key in keys}:
        plan.errors.append(f"Project Key {row.project_key!r} doesn't match project {project.pk}'s, {keys}")
    if project.status.name != "Active":
        plan.errors.append(f"project {project.pk} is {project.status.name}, not Active")
    return None if plan.errors else project


def plan_attributes(plan: ProjectPlan) -> None:
    """Plan setting users_managed and Group to the row's values, replacing any others."""
    row, project = plan.row, plan.project
    for name, value in ((USERS_MANAGED_ATTRIBUTE, row.user_managed), (GROUP_ATTRIBUTE, row.group)):
        if not ProjectAttributeType.objects.filter(name=name).exists():
            plan.errors.append(f"there's no {name} project attribute type")
            continue
        if not value:
            plan.errors.append(f"the row has no {name}")
            continue
        current = attribute_values(project, name)
        if current != [value]:
            plan.attribute_changes[name] = (current, value)
    others = (
        ProjectAttribute.objects.filter(proj_attr_type__name=GROUP_ATTRIBUTE, value__iexact=row.group)
        .exclude(project=project)
        .values_list("project_id", flat=True)
    )
    if row.group and others:
        plan.errors.append(f"group {row.group!r} is already project {', '.join(map(str, sorted(others)))}'s Group")


def look_up_users_in_ad(plan: ProjectPlan, usernames: list[str]) -> dict[str, dict]:
    """Each user's AD entry, by username. Users who aren't in AD are noted, and left out; failing to ask
    AD is an error."""
    entries, missing = {}, set()
    for username in usernames:
        try:
            entry = find_ad_user(username)
        except Exception as e:
            plan.errors.append(f"couldn't look {username} up in AD: {e!r}")
            continue
        if entry is None:
            missing.add(username)
        else:
            entries[username] = entry
    note_usernames(plan, "in the CSV's Users but not in AD (not added)", missing)
    return entries


def plan_members(plan: ProjectPlan, ad_entries: dict, approvers: set) -> None:
    """Plan making each of the CSV's users found in AD an Active member with the role their tags give. The
    PI is left alone; active members not in the CSV are noted."""
    project = plan.project
    pi = project.pi.username.lower()
    existing = {pu.user.username.lower(): pu for pu in project.projectuser_set.select_related("user", "role", "status")}
    for username, entry in ad_entries.items():
        if username == pi:
            continue
        role = "Manager" if username in approvers else "User"
        project_user = existing.get(username)
        if project_user is None:
            plan.member_changes.append(MemberChange(username, role, entry, f"add {username} as {role}"))
        elif project_user.role.name != role or project_user.status.name != "Active":
            was = f"{project_user.role.name}, {project_user.status.name}"
            plan.member_changes.append(
                MemberChange(username, role, entry, f"make {username} an Active {role} (was {was})")
            )
    not_in_csv = {u for u, pu in existing.items() if pu.status.name == "Active" and u not in plan.row.users and u != pi}
    note_usernames(plan, "active members not in the CSV's Users (left as they are)", not_in_csv)


def active_members_after(plan: ProjectPlan) -> set[str]:
    """The usernames of the project's active members once the plan is applied."""
    return {
        username.lower()
        for username in plan.project.projectuser_set.filter(status__name="Active").values_list(
            "user__username", flat=True
        )
    } | {change.username for change in plan.member_changes}


def plan_allocation_users(plan: ProjectPlan) -> None:
    """Plan making every member who'll be active an active user of each of the project's Active allocations.
    Active allocation users who won't be members are noted."""
    project = plan.project
    active_members = active_members_after(plan)
    for allocation in project.allocation_set.filter(status__name="Active").order_by("pk"):
        allocation_users = {
            username.lower()
            for username in allocation.allocationuser_set.filter(status__name="Active").values_list(
                "user__username", flat=True
            )
        }
        for username in sorted(active_members - allocation_users):
            plan.allocation_additions.append((allocation, username))
        note_usernames(
            plan,
            f"active users of allocation {allocation.pk} who aren't active members (left as they are)",
            allocation_users - active_members,
        )


def attribute_values(project, name: str) -> list[str]:
    return sorted(project.projectattribute_set.filter(proj_attr_type__name=name).values_list("value", flat=True))


def note_usernames(plan: ProjectPlan, what: str, usernames: set) -> None:
    if usernames:
        plan.notes.append(f"{what}: {', '.join(sorted(usernames))}")


def apply_plan(plan: ProjectPlan) -> list[str]:
    """Make the plan's changes, and return what went wrong adding users to the Grouper group, if anything.
    Only for a valid plan.

    ColdFront's changes are made in one transaction, inside roles_from_starfish(): the roles come from
    Starfish's tags, so writing them back is not queued. The Grouper group's are made once they're
    committed, since Grouper's can't be rolled back with them; running the import again retries any that
    failed.
    """
    if not plan.is_valid:
        raise ValueError(f"Line {plan.row.line}'s plan has errors: {plan.errors}")
    project = plan.project
    active = ProjectUserStatusChoice.objects.get(name="Active")
    active_allocation_user = AllocationUserStatusChoice.objects.get(name="Active")
    with transaction.atomic(), roles_from_starfish():
        for name, (_, value) in plan.attribute_changes.items():
            set_project_attribute(project, name, value)
        for change in plan.member_changes:
            ProjectUser.objects.update_or_create(
                project=project,
                user=user_from_ad_entry(change.ad_entry),
                defaults={"role": ProjectUserRoleChoice.objects.get(name=change.role), "status": active},
            )
        for allocation, username in plan.allocation_additions:
            allocation.allocationuser_set.update_or_create(
                user=get_user_model().objects.get(username__iexact=username),
                defaults={"status": active_allocation_user},
            )
    return add_to_grouper_group(plan.row.group, plan.grouper_additions)


def add_to_grouper_group(group: str, usernames: list[str]) -> list[str]:
    """Add the users to the Grouper group, and update its cached members with those added. Returns what
    went wrong, if anything."""
    if not usernames:
        return []
    try:
        outcomes = get_grouper_client().add_members_to_group(group, usernames)
    except Exception as e:
        return [f"couldn't add {', '.join(usernames)} to Grouper group {group!r}: {e!r}"]
    succeeded = {username.lower() for username, success in outcomes.items() if success}
    added = [username for username in usernames if username in succeeded]
    members = grouper_group_members(group) or []
    remember_group_members(group, sorted(set(members) | set(added)))
    failed = [username for username in usernames if username not in succeeded]
    return [f"Grouper didn't add {', '.join(failed)} to group {group!r}"] if failed else []


def set_project_attribute(project, name: str, value: str) -> None:
    """Make value the project's only attribute of the type, keeping the first existing one's row."""
    attributes = list(project.projectattribute_set.filter(proj_attr_type__name=name).order_by("pk"))
    if not attributes:
        ProjectAttribute.objects.create(
            project=project, proj_attr_type=ProjectAttributeType.objects.get(name=name), value=value
        )
        return
    first, *rest = attributes
    first.value = value
    first.save()
    for attribute in rest:
        attribute.delete()


def user_from_ad_entry(entry: dict):
    """The ColdFront user for an AD entry, created if need be, with their name and email filled in if blank,
    as utils.create_user does."""
    User = get_user_model()
    user = User.objects.filter(username__iexact=entry["username"]).first()
    if user is None:
        user = User.objects.create(username=entry["username"].lower())
    if not user.first_name or not user.last_name or not user.email:
        user.first_name = entry.get("first_name") or user.first_name
        user.last_name = entry.get("last_name") or user.last_name
        user.email = entry.get("email") or user.email
        user.save()
    return user


def describe_plan(plan: ProjectPlan) -> str:
    """The plan as text for the command's output: the project, its errors, changes and notes."""
    row = plan.row
    lines = [
        f"Line {row.line}: project {row.project_id}, {row.name} ({row.project_key}): {'OK' if plan.is_valid else 'INVALID'}"
    ]
    lines += [f"  error: {error}" for error in plan.errors]
    for name, (current, value) in plan.attribute_changes.items():
        lines.append(f"  change: set {name} to {value!r} (was {', '.join(map(repr, current)) or 'unset'})")
    lines += [f"  change: {change.description}" for change in plan.member_changes]
    if plan.grouper_additions:
        lines.append(f"  change: add {', '.join(plan.grouper_additions)} to Grouper group {plan.row.group!r}")
    for allocation, username in plan.allocation_additions:
        resources = ", ".join(allocation.resources.values_list("name", flat=True))
        lines.append(f"  change: make {username} an active user of allocation {allocation.pk} ({resources})")
    lines += [f"  note: {note}" for note in plan.notes]
    return "\n".join(lines)
