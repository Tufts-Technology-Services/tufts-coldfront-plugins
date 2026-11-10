# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import dataclass

from django.db.models import Q
from django.urls import Resolver404, resolve, reverse

from coldfront.core.allocation.models import Allocation
from coldfront.core.project.models import Project, ProjectUser

from tufts_local.su import SU_READ_ONLY_SESSION_KEY

# the pages the control is on, by URL name
VIEW_AS_PAGES = ("project-detail", "allocation-detail")

# who counts as a member of a project, as ColdFront's own permission check counts them
MEMBER_STATUSES = ("Active", "New")

# who counts as being on an allocation, as ColdFront's Allocation.user_permissions counts them
ALLOCATION_USER_STATUSES = ("Active", "New", "PendingEULA")


# The kinds of member offered, in ColdFront's own words. What a site calls them is for its templates
# to say: Tufts shows "Owner" for "pi" and "Approver" for "manager", in coldfront-branding.
KIND_PI = "pi"
KIND_MANAGER = "manager"
KIND_USER = "user"


@dataclass(frozen=True)
class ViewAsPerson:
    """Somebody a superuser can view the page as, and which kind of member they are: pi, manager or user."""

    user: object
    kind: str


def _pages_project_and_allocation(match):
    """The project a detail page is of, and its allocation where it is an allocation's page."""
    pk = match.kwargs.get("pk")
    if match.url_name == "project-detail":
        return Project.objects.filter(pk=pk).first(), None
    allocation = Allocation.objects.select_related("project").filter(pk=pk).first()
    return (allocation.project, allocation) if allocation else (None, None)


def _representatives(project, allocation, excluding):
    """
    Three of the project's members to view the page as: the PI, a manager and a user.

    The PI is the project's pi. A manager is a Manager who isn't the PI, and a user is anyone else,
    the kinds of member that can see a page differently. One of each is enough to see what each sees, so
    that is all that is offered, the first by username so it is the same every time. Only members who
    can open the page are considered: all of them for a project's page, and for an allocation's, the
    project's Managers and PI, and a regular member only if they are on the allocation. Any other
    member gets a 403, which is what viewing as them would show, but isn't worth offering. The user
    looking is never offered.
    """
    members = ProjectUser.objects.filter(
        project=project, status__name__in=MEMBER_STATUSES, user__is_active=True
    ).select_related("user", "role")
    if allocation is not None:
        on_allocation = allocation.allocationuser_set.filter(status__name__in=ALLOCATION_USER_STATUSES)
        members = members.filter(Q(role__name="Manager") | Q(user_id__in=on_allocation.values("user_id")))
    candidates = [m for m in members.order_by("user__username") if m.user_id != excluding.pk]

    pi = next((m for m in candidates if m.user_id == project.pi_id), None)
    manager = next((m for m in candidates if m.user_id != project.pi_id and m.role.name == "Manager"), None)
    user = next((m for m in candidates if m.user_id != project.pi_id and m.role.name != "Manager"), None)
    return [
        ViewAsPerson(user=membership.user, kind=kind)
        for kind, membership in ((KIND_PI, pi), (KIND_MANAGER, manager), (KIND_USER, user))
        if membership is not None
    ]


def view_as(request):
    """
    What the navbar's "View as" control needs, as `view_as`, or nothing where there is no control.

    Only on a project or allocation detail page, and only for a superuser. Not signed in as someone:
    `people`, a PI, a manager and a user who can open this page (each with a `kind`: "pi", "manager"
    or "user"), and `start_url`, `next` (this page) to post.
    Viewing as someone, read-only: `viewing_as`, that user, and `exit_url`, which releases them and
    comes back to this page. A full sign in as someone, with "Login as...", has no control: it has
    its own way back.
    """
    # coldfront-utils 0.2.0 or later; see tufts_local.checks.check_view_as_dependencies
    from coldfront_utils.task_context.su import signed_in_by

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    administrator = signed_in_by(request)
    viewing = administrator is not None and bool(request.session.get(SU_READ_ONLY_SESSION_KEY))
    if administrator is not None and not viewing:
        return {}
    if not (administrator or user).is_superuser:
        return {}
    try:
        match = resolve(request.path_info)
    except Resolver404:
        return {}
    if match.url_name not in VIEW_AS_PAGES:
        return {}
    project, allocation = _pages_project_and_allocation(match)
    if project is None:
        return {}
    return {
        "view_as": {
            "start_url": reverse("view-as"),
            "exit_url": reverse("view-as-exit"),
            "next": request.get_full_path(),
            "viewing_as": user if viewing else None,
            "people": [] if viewing else _representatives(project, allocation, excluding=user),
        }
    }
