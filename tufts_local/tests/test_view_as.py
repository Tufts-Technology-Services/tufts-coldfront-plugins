# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
"View as": a superuser looks at a project or allocation page as one of its members, through django-su
but read-only, and comes back to the page afterwards.
"""

import logging

import pytest
from django.contrib.auth import SESSION_KEY, get_user_model
from django.test import RequestFactory
from django.urls import reverse

from coldfront.core.allocation.models import (
    Allocation,
    AllocationStatusChoice,
    AllocationUser,
    AllocationUserStatusChoice,
)
from coldfront.core.project.models import ProjectUser, ProjectUserStatusChoice

from tufts_local.context_processors import ViewAsPerson, view_as
from tufts_local.middleware import SuReadOnlyMiddleware
from tufts_local.su import SU_READ_ONLY_SESSION_KEY

BACKEND = "django.contrib.auth.backends.ModelBackend"


def make_account(username, **fields):
    return get_user_model().objects.create(username=username, **fields)


@pytest.fixture
def admin(db):
    return make_account("rdms_admin", is_superuser=True, is_staff=True)


@pytest.fixture
def manager(project, roles, statuses):
    account = make_account("mgr01")
    ProjectUser.objects.create(project=project, user=account, role=roles["Manager"], status=statuses["Active"])
    return account


@pytest.fixture
def member(project, roles, statuses):
    account = make_account("user01")
    ProjectUser.objects.create(project=project, user=account, role=roles["User"], status=statuses["Active"])
    return account


@pytest.fixture
def allocation(project):
    return Allocation.objects.create(
        project=project, status=AllocationStatusChoice.objects.get_or_create(name="Active")[0], justification="research"
    )


@pytest.fixture
def read_only_enforced(settings):
    """The middleware where production puts it."""
    settings.MIDDLEWARE = [*settings.MIDDLEWARE, "tufts_local.middleware.SuReadOnlyMiddleware"]


def su_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "tufts_local.su"]


# --- refusing changes ---------------------------------------------------------------------------


def request_with_flag(method, path="/project/1/", viewing=True):
    request = getattr(RequestFactory(), method)(path)
    request.session = {SU_READ_ONLY_SESSION_KEY: True} if viewing else {}
    return request


@pytest.mark.urls("tufts_local.tests.urls")
class TestSuReadOnlyMiddleware:
    @staticmethod
    def respond(request):
        reached = []

        def view(request):
            reached.append(True)
            from django.http import HttpResponse

            return HttpResponse("ok")

        return SuReadOnlyMiddleware(view)(request), bool(reached)

    @pytest.mark.parametrize("method", ["get", "head", "options"])
    def test_lets_a_read_through(self, method):
        response, reached = self.respond(request_with_flag(method))

        assert reached
        assert response.status_code == 200

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_refuses_anything_that_could_change_something(self, method):
        response, reached = self.respond(request_with_flag(method))

        assert not reached
        assert response.status_code == 403

    def test_says_why_and_what_to_do(self):
        response, _ = self.respond(request_with_flag("post"))

        assert "read-only" in response.content.decode().lower()
        assert "release" in response.content.decode().lower()

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_changes_are_fine_when_nobody_is_being_viewed_as(self, method):
        response, reached = self.respond(request_with_flag(method, viewing=False))

        assert reached
        assert response.status_code == 200

    def test_a_request_with_no_session_is_left_alone(self):
        request = RequestFactory().post("/project/1/")

        response, reached = self.respond(request)

        assert reached

    def test_signing_out_is_let_through_so_the_session_can_always_be_ended(self):
        response, reached = self.respond(request_with_flag("post", path=reverse("logout")))

        assert reached

    def test_signing_out_is_let_through_even_with_an_empty_allowlist(self, settings):
        settings.VIEW_AS_READ_ONLY_ALLOWED_POSTS = []

        response, reached = self.respond(request_with_flag("post", path=reverse("logout")))

        assert reached

    def test_by_default_the_add_users_search_results_page_is_let_through(self):
        """A POST that only shows matches, the step of the add-users flow before anything is added."""
        response, reached = self.respond(
            request_with_flag("post", path=reverse("project-add-users-search-results", args=[1]))
        )

        assert reached

    def test_but_adding_the_users_is_not(self):
        response, reached = self.respond(request_with_flag("post", path=reverse("project-add-users", args=[1])))

        assert not reached
        assert response.status_code == 403

    def test_the_allowlist_comes_from_settings(self, settings):
        settings.VIEW_AS_READ_ONLY_ALLOWED_POSTS = ["project-archive"]

        archive, archive_reached = self.respond(request_with_flag("post", path=reverse("project-archive", args=[1])))
        search, search_reached = self.respond(
            request_with_flag("post", path=reverse("project-add-users-search-results", args=[1]))
        )

        assert archive_reached
        # the setting replaces the default rather than adding to it
        assert not search_reached

    def test_a_namespaced_name_is_matched_whole(self, settings):
        settings.VIEW_AS_READ_ONLY_ALLOWED_POSTS = ["plugin_test:report"]

        response, reached = self.respond(request_with_flag("post", path=reverse("plugin_test:report")))

        assert reached

    def test_a_bare_name_does_not_match_a_namespaced_view(self, settings):
        settings.VIEW_AS_READ_ONLY_ALLOWED_POSTS = ["report"]

        response, reached = self.respond(request_with_flag("post", path=reverse("plugin_test:report")))

        assert not reached

    def test_a_path_that_matches_no_page_is_refused(self):
        response, reached = self.respond(request_with_flag("post", path="/not/a/page/"))

        assert not reached
        assert response.status_code == 403


# --- starting and stopping ----------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.urls("tufts_local.tests.urls")
@pytest.mark.usefixtures("su_enabled")
class TestStartViewingAs:
    def test_signs_in_as_the_user_and_returns_to_the_page(self, client, admin, member, project):
        client.force_login(admin)
        page = reverse("project-detail", args=[project.pk])

        response = client.post(reverse("view-as"), {"user": member.pk, "next": page})

        assert response.status_code == 302
        assert response["Location"] == page
        assert client.session[SESSION_KEY] == str(member.pk)

    def test_marks_the_session_read_only_and_remembers_the_administrator(self, client, admin, member):
        client.force_login(admin)

        client.post(reverse("view-as"), {"user": member.pk, "next": "/"})

        assert client.session[SU_READ_ONLY_SESSION_KEY] is True
        assert client.session["exit_users_pk"][0][0] == str(admin.pk)

    def test_the_log_says_it_is_read_only(self, client, admin, member, caplog):
        client.force_login(admin)

        with caplog.at_level(logging.INFO, logger="tufts_local.su"):
            client.post(reverse("view-as"), {"user": member.pk, "next": "/"})

        [line] = su_lines(caplog)
        assert "admin=rdms_admin" in line
        assert "target=user01" in line
        assert "mode=read-only" in line

    @pytest.mark.parametrize("unsafe", ["https://evil.example/", "//evil.example/", "javascript:alert(1)"])
    def test_goes_home_rather_than_to_another_site(self, client, admin, member, unsafe):
        client.force_login(admin)

        response = client.post(reverse("view-as"), {"user": member.pk, "next": unsafe})

        assert response["Location"] == "/"

    def test_goes_home_when_not_told_where_to(self, client, admin, member):
        client.force_login(admin)

        assert client.post(reverse("view-as"), {"user": member.pk})["Location"] == "/"

    def test_only_superusers_may(self, client, member, settings):
        # coldfront's own callback logs a denial with "{}" in a %-style message, which fails to format
        settings.SU_LOGIN_CALLBACK = lambda user: user.is_active and user.is_superuser
        client.force_login(make_account("staff01", is_staff=True))

        client.post(reverse("view-as"), {"user": member.pk, "next": "/"})

        assert client.session[SESSION_KEY] != str(member.pk)
        assert SU_READ_ONLY_SESSION_KEY not in client.session

    def test_a_user_nobody_has_is_not_found(self, client, admin):
        client.force_login(admin)

        assert client.post(reverse("view-as"), {"user": 999999, "next": "/"}).status_code == 404

    def test_a_get_is_not_allowed(self, client, admin):
        client.force_login(admin)

        assert client.get(reverse("view-as")).status_code == 405

    @pytest.mark.usefixtures("read_only_enforced")
    def test_nothing_can_be_changed_afterwards(self, client, admin, member, project):
        client.force_login(admin)
        client.post(reverse("view-as"), {"user": member.pk, "next": "/"})

        response = client.post(reverse("project-archive", args=[project.pk]))

        assert response.status_code == 403
        project.refresh_from_db()
        assert project.status.name == "Active"

    @pytest.mark.usefixtures("read_only_enforced")
    def test_a_viewed_as_session_cannot_start_another(self, client, admin, member, manager):
        client.force_login(admin)
        client.post(reverse("view-as"), {"user": member.pk, "next": "/"})

        response = client.post(reverse("view-as"), {"user": manager.pk, "next": "/"})

        assert response.status_code == 403
        assert client.session[SESSION_KEY] == str(member.pk)


@pytest.mark.django_db
@pytest.mark.urls("tufts_local.tests.urls")
@pytest.mark.usefixtures("su_enabled")
class TestStopViewingAs:
    @pytest.fixture
    def viewing(self, client, admin, member):
        client.force_login(admin)
        client.post(reverse("view-as"), {"user": member.pk, "next": "/"})
        return client

    def test_signs_back_in_as_the_administrator_and_returns_to_the_page(self, viewing, admin, project):
        page = reverse("project-detail", args=[project.pk])

        response = viewing.get(reverse("view-as-exit"), {"next": page})

        assert response["Location"] == page
        assert viewing.session[SESSION_KEY] == str(admin.pk)

    def test_the_read_only_mark_goes_with_the_session(self, viewing):
        viewing.get(reverse("view-as-exit"), {"next": "/"})

        assert SU_READ_ONLY_SESSION_KEY not in viewing.session

    def test_the_log_says_who_was_released(self, viewing, caplog):
        with caplog.at_level(logging.INFO, logger="tufts_local.su"):
            viewing.get(reverse("view-as-exit"), {"next": "/"})

        [line] = su_lines(caplog)
        assert line.startswith("su end:")
        assert "released=user01" in line
        assert "back_to=rdms_admin" in line

    @pytest.mark.parametrize("unsafe", ["https://evil.example/", "//evil.example/"])
    def test_goes_home_rather_than_to_another_site(self, viewing, unsafe):
        assert viewing.get(reverse("view-as-exit"), {"next": unsafe})["Location"] == "/"

    def test_nothing_to_release_is_a_bad_request(self, client, admin):
        client.force_login(admin)

        assert client.get(reverse("view-as-exit"), {"next": "/"}).status_code == 400

    @pytest.mark.usefixtures("read_only_enforced")
    def test_releasing_is_allowed_while_everything_else_is_refused(self, viewing, admin):
        response = viewing.get(reverse("view-as-exit"), {"next": "/"})

        assert response.status_code == 302
        assert viewing.session[SESSION_KEY] == str(admin.pk)


# --- the control's data -------------------------------------------------------------------------


def page_request(url_name, args, user, remembered=(), read_only=False, query=""):
    request = RequestFactory().get(reverse(url_name, args=args) + query)
    request.user = user
    session = {"exit_users_pk": [[str(before.pk), BACKEND] for before in remembered]}
    if read_only:
        session[SU_READ_ONLY_SESSION_KEY] = True
    request.session = session
    return request


def people_of(context):
    return {person.user.username: person.kind for person in context["view_as"]["people"]}


@pytest.mark.django_db
@pytest.mark.urls("tufts_local.tests.urls")
class TestViewAsContext:
    def test_a_project_page_offers_the_pi_a_manager_and_a_user(self, admin, project, manager, member):
        context = view_as(page_request("project-detail", [project.pk], admin))

        assert people_of(context) == {"pi01": "pi", "mgr01": "manager", "user01": "user"}

    def test_only_one_of_each_kind_is_offered_the_first_by_username(self, admin, project, roles, statuses):
        for username, role in (
            ("mgr03", "Manager"),
            ("mgr02", "Manager"),
            ("user03", "User"),
            ("user02", "User"),
        ):
            ProjectUser.objects.create(
                project=project, user=make_account(username), role=roles[role], status=statuses["Active"]
            )

        context = view_as(page_request("project-detail", [project.pk], admin))

        assert people_of(context) == {"pi01": "pi", "mgr02": "manager", "user02": "user"}

    def test_the_pi_is_not_also_offered_as_a_manager(self, admin, project):
        """The PI has a Manager row, as ColdFront makes it, but is offered as the PI, not as a manager."""
        context = view_as(page_request("project-detail", [project.pk], admin))

        assert people_of(context) == {"pi01": "pi"}

    def test_a_pi_whose_own_role_is_user_is_still_the_pi(self, admin, project, roles):
        project.projectuser_set.filter(user=project.pi).update(role=roles["User"])

        context = view_as(page_request("project-detail", [project.pk], admin))

        assert people_of(context) == {"pi01": "pi"}

    def test_a_pi_who_is_not_a_member_cannot_open_the_page_so_is_not_offered(self, admin, project, manager, member):
        project.projectuser_set.filter(user=project.pi).delete()

        context = view_as(page_request("project-detail", [project.pk], admin))

        assert people_of(context) == {"mgr01": "manager", "user01": "user"}

    def test_an_administrator_who_is_also_a_member_is_not_offered_to_themselves(
        self, project, roles, statuses, manager
    ):
        also_manager = make_account("aaa_admin", is_superuser=True)
        ProjectUser.objects.create(project=project, user=also_manager, role=roles["Manager"], status=statuses["Active"])

        context = view_as(page_request("project-detail", [project.pk], also_manager))

        # aaa_admin sorts first, so would have been the manager; the next is offered instead
        assert people_of(context) == {"pi01": "pi", "mgr01": "manager"}

    def test_a_member_who_is_new_counts_and_one_removed_does_not(self, admin, project, roles, statuses):
        new = make_account("new01")
        gone = make_account("gone01")
        ProjectUser.objects.create(
            project=project,
            user=new,
            role=roles["User"],
            status=ProjectUserStatusChoice.objects.get_or_create(name="New")[0],
        )
        ProjectUser.objects.create(project=project, user=gone, role=roles["User"], status=statuses["Removed"])

        context = view_as(page_request("project-detail", [project.pk], admin))

        assert people_of(context) == {"pi01": "pi", "new01": "user"}

    def test_an_allocation_page_lists_the_managers_and_those_on_the_allocation(
        self, admin, project, allocation, manager, member
    ):
        context = view_as(page_request("allocation-detail", [allocation.pk], admin))

        # a regular member who is not on the allocation could not open the page, so isn't offered
        assert people_of(context) == {"pi01": "pi", "mgr01": "manager"}

    def test_and_a_regular_member_who_is_on_it(self, admin, project, allocation, member):
        AllocationUser.objects.create(
            allocation=allocation,
            user=member,
            status=AllocationUserStatusChoice.objects.get_or_create(name="Active")[0],
        )

        context = view_as(page_request("allocation-detail", [allocation.pk], admin))

        assert people_of(context)["user01"] == "user"

    def test_the_user_offered_is_one_who_is_on_the_allocation_not_just_the_first_member(
        self, admin, project, allocation, member, roles, statuses
    ):
        """user00 sorts before user01 but could not open the page."""
        ProjectUser.objects.create(
            project=project, user=make_account("user00"), role=roles["User"], status=statuses["Active"]
        )
        AllocationUser.objects.create(
            allocation=allocation,
            user=member,
            status=AllocationUserStatusChoice.objects.get_or_create(name="Active")[0],
        )

        context = view_as(page_request("allocation-detail", [allocation.pk], admin))

        assert people_of(context)["user01"] == "user"
        assert "user00" not in people_of(context)

    def test_but_not_one_whose_place_on_the_allocation_was_removed(self, admin, project, allocation, member):
        AllocationUser.objects.create(
            allocation=allocation,
            user=member,
            status=AllocationUserStatusChoice.objects.get_or_create(name="Removed")[0],
        )

        context = view_as(page_request("allocation-detail", [allocation.pk], admin))

        assert "user01" not in people_of(context)

    def test_says_where_to_post_and_which_page_to_come_back_to(self, admin, project):
        context = view_as(page_request("project-detail", [project.pk], admin, query="?tab=users"))

        assert context["view_as"]["start_url"] == reverse("view-as")
        assert context["view_as"]["next"] == reverse("project-detail", args=[project.pk]) + "?tab=users"
        assert context["view_as"]["viewing_as"] is None

    def test_people_are_offered_as_a_list_of_users_and_roles(self, admin, project):
        [person] = view_as(page_request("project-detail", [project.pk], admin))["view_as"]["people"]

        assert person == ViewAsPerson(user=project.pi, kind="pi")

    def test_the_pi_comes_first_then_the_manager_then_the_user(self, admin, project, manager, member):
        people = view_as(page_request("project-detail", [project.pk], admin))["view_as"]["people"]

        assert [person.kind for person in people] == ["pi", "manager", "user"]

    @pytest.mark.parametrize("flags", [{"is_staff": True}, {}])
    def test_only_for_superusers(self, project, flags):
        context = view_as(page_request("project-detail", [project.pk], make_account("staff01", **flags)))

        assert context == {}

    def test_not_for_an_anonymous_visitor(self, project):
        from django.contrib.auth.models import AnonymousUser

        assert view_as(page_request("project-detail", [project.pk], AnonymousUser())) == {}

    @pytest.mark.parametrize("page", ["project-list", "allocation-list", "user-profile"])
    def test_only_on_the_project_and_allocation_detail_pages(self, admin, page):
        request = RequestFactory().get(reverse(page))
        request.user = admin
        request.session = {}

        assert view_as(request) == {}

    def test_a_page_of_a_project_that_does_not_exist_has_no_control(self, admin):
        assert view_as(page_request("project-detail", [999999], admin)) == {}

    def test_while_viewing_as_someone_it_says_so_and_offers_the_way_back(self, admin, project, member):
        request = page_request("project-detail", [project.pk], member, remembered=[admin], read_only=True)

        context = view_as(request)["view_as"]

        assert context["viewing_as"] == member
        assert context["people"] == []
        assert context["exit_url"] == reverse("view-as-exit")
        assert context["next"] == reverse("project-detail", args=[project.pk])

    def test_a_full_sign_in_as_someone_has_no_control(self, admin, project, member):
        """Not read-only: "Login as..." has its own way back, and no switching from inside."""
        request = page_request("project-detail", [project.pk], member, remembered=[admin], read_only=False)

        assert view_as(request) == {}
