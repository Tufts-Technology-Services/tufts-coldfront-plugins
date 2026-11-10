# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
from unittest import mock

import pytest
from django.contrib.auth import SESSION_KEY, get_user_model
from django.test import RequestFactory

from tufts_local import su
from tufts_local.su import describe_su_change, log_su_login

BACKEND = "django.contrib.auth.backends.ModelBackend"


def make_account(username, **fields):
    return get_user_model().objects.create(username=username, **fields)


@pytest.fixture
def admin(db):
    return make_account("rdms_admin", is_superuser=True, is_staff=True)


@pytest.fixture
def other_admin(db):
    return make_account("rdms_admin2", is_superuser=True, is_staff=True)


@pytest.fixture
def pi(db):
    return make_account("pi01")


def su_records(caplog):
    return [record.getMessage() for record in caplog.records if record.name == "tufts_local.su"]


def request_to(path, user, remembered=(), **extra):
    """A request to `path` by `user`, in a session django-su remembers `remembered` in, oldest first."""
    request = RequestFactory().get(path, **extra)
    request.user = user
    request.session = {"exit_users_pk": [[str(before.pk), BACKEND] for before in remembered]}
    return request


@pytest.mark.django_db
@pytest.mark.urls("tufts_local.tests.urls")
@pytest.mark.usefixtures("su_enabled")
class TestThroughDjangoSu:
    """django-su's real views, so what they pass to the action is what they really pass."""

    def test_signing_in_as_someone_is_logged_with_who_whom_and_the_mode(self, client, admin, pi, caplog):
        client.force_login(admin)

        with caplog.at_level(logging.INFO, logger="tufts_local.su"):
            client.post(f"/su/{pi.pk}/")

        [line] = su_records(caplog)
        assert line.startswith("su start:")
        assert "admin=rdms_admin" in line
        assert "target=pi01" in line
        assert "mode=full" in line

    def test_the_administrator_is_still_signed_in_as_them_afterwards(self, client, admin, pi):
        client.force_login(admin)

        client.post(f"/su/{pi.pk}/")

        assert client.session[SESSION_KEY] == str(pi.pk)
        assert client.session["exit_users_pk"][0][0] == str(admin.pk)

    def test_releasing_them_is_logged(self, client, admin, pi, caplog):
        client.force_login(admin)
        client.post(f"/su/{pi.pk}/")

        with caplog.at_level(logging.INFO, logger="tufts_local.su"):
            client.get("/su/")

        [line] = su_records(caplog)
        assert line.startswith("su end:")
        assert "admin=rdms_admin" in line
        assert "released=pi01" in line
        assert "back_to=rdms_admin" in line

    def test_and_the_administrator_is_back_afterwards(self, client, admin, pi):
        client.force_login(admin)
        client.post(f"/su/{pi.pk}/")

        client.get("/su/")

        assert client.session[SESSION_KEY] == str(admin.pk)

    def test_a_second_sign_in_from_the_signed_in_account_still_names_the_first_administrator(
        self, client, admin, other_admin, pi, caplog
    ):
        client.force_login(admin)
        client.post(f"/su/{other_admin.pk}/")

        with caplog.at_level(logging.INFO, logger="tufts_local.su"):
            client.post(f"/su/{pi.pk}/")

        [line] = su_records(caplog)
        assert "admin=rdms_admin" in line
        assert "target=pi01" in line

    def test_releasing_one_of_two_names_where_they_go_back_to(self, client, admin, other_admin, pi, caplog):
        client.force_login(admin)
        client.post(f"/su/{other_admin.pk}/")
        client.post(f"/su/{pi.pk}/")

        with caplog.at_level(logging.INFO, logger="tufts_local.su"):
            client.get("/su/")

        [line] = su_records(caplog)
        assert "admin=rdms_admin" in line
        assert "released=pi01" in line
        assert "back_to=rdms_admin2" in line

    def test_a_user_who_may_not_sign_in_as_anyone_leaves_no_record_and_is_not_signed_in_as_them(
        self, client, pi, caplog, settings
    ):
        # coldfront's own callback logs a denial with "{}" in a %-style message, which fails to format
        settings.SU_LOGIN_CALLBACK = lambda user: user.is_active and user.is_superuser
        client.force_login(make_account("staff01", is_staff=True))

        with caplog.at_level(logging.INFO, logger="tufts_local.su"):
            client.post(f"/su/{pi.pk}/")

        assert su_records(caplog) == []
        assert client.session[SESSION_KEY] != str(pi.pk)


class TestDescribeSuChange:
    def test_the_login_page_counts_as_a_start_too(self, admin, pi):
        line = describe_su_change(request_to("/su/login/", admin), pi)

        assert line.startswith("su start:")
        assert "target=pi01" in line

    def test_a_request_marked_read_only_is_logged_as_that(self, admin, pi):
        request = request_to(f"/su/{pi.pk}/", admin)
        request.su_read_only = True

        assert "mode=read-only" in describe_su_change(request, pi)

    def test_says_where_the_request_came_from(self, admin, pi):
        request = request_to(
            f"/su/{pi.pk}/", admin, REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="130.64.1.2, 10.9.8.7"
        )

        line = describe_su_change(request, pi)

        assert "forwarded_for='130.64.1.2, 10.9.8.7'" in line
        assert "remote_addr='127.0.0.1'" in line

    def test_a_request_with_no_forwarded_for_says_so(self, admin, pi):
        line = describe_su_change(request_to(f"/su/{pi.pk}/", admin, REMOTE_ADDR="127.0.0.1"), pi)

        assert "forwarded_for=None" in line

    def test_a_header_with_a_line_break_cannot_start_another_log_line(self, admin, pi):
        request = request_to(f"/su/{pi.pk}/", admin, HTTP_X_FORWARDED_FOR="1.2.3.4\nsu start: admin=forged")

        line = describe_su_change(request, pi)

        assert "\n" not in line

    def test_a_path_that_matches_no_page_is_taken_as_a_start(self, admin, pi):
        assert describe_su_change(request_to("/not/a/page/", admin), pi).startswith("su start:")


class TestLogSuLogin:
    def test_signs_in_as_the_user_after_logging(self, admin, pi, caplog):
        request = request_to(f"/su/{pi.pk}/", admin)

        with mock.patch.object(su, "login") as sign_in, caplog.at_level(logging.INFO, logger="tufts_local.su"):
            log_su_login(request, pi)

        sign_in.assert_called_once_with(request, pi)
        assert len(su_records(caplog)) == 1

    def test_signs_in_even_when_logging_fails(self, admin, pi):
        request = request_to(f"/su/{pi.pk}/", admin)
        broken_logger = mock.Mock()
        broken_logger.info.side_effect = OSError("disk full")

        with mock.patch.object(su, "login") as sign_in, mock.patch.object(su, "logger", broken_logger):
            log_su_login(request, pi)

        sign_in.assert_called_once_with(request, pi)
        broken_logger.exception.assert_called_once()
