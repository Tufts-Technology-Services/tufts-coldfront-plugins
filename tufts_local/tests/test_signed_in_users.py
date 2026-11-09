# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import timedelta

import pytest
from django.contrib.auth import BACKEND_SESSION_KEY, SESSION_KEY, get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.utils import timezone

from tufts_local.signed_in_users import get_signed_in_users

BACKEND = "django.contrib.auth.backends.ModelBackend"


def make_person(username, **fields):
    return get_user_model().objects.create(username=username, **fields)


def start_session(user=None, minutes_since_last_request=0, extra=None, settings=None):
    """
    Save a session as a request by `user` would have left it, `minutes_since_last_request` ago.

    With no user it's an anonymous session. `extra` adds other keys, e.g. django-su's.
    """
    store = SessionStore()
    if user is not None:
        store[SESSION_KEY] = str(user.pk)
        store[BACKEND_SESSION_KEY] = BACKEND
    store.update(extra or {})
    store.save()
    last_request = timezone.now() - timedelta(minutes=minutes_since_last_request)
    expiry = last_request + timedelta(seconds=settings.SESSION_COOKIE_AGE)
    Session.objects.filter(session_key=store.session_key).update(expire_date=expiry)
    return last_request


def summary(rows):
    return [(row.user.username, row.session_count, row.logged_in_as_by) for row in rows]


@pytest.mark.django_db
class TestGetSignedInUsers:
    def test_lists_a_user_with_an_unexpired_session_and_when_they_last_made_a_request(self, settings):
        alice = make_person("alice")
        last_request = start_session(alice, minutes_since_last_request=5, settings=settings)

        [row] = get_signed_in_users()

        assert row.user == alice
        assert abs(row.last_request - last_request) < timedelta(seconds=1)
        assert row.session_count == 1
        assert row.logged_in_as_by == ()

    def test_leaves_out_expired_sessions(self, settings):
        settings.SESSION_COOKIE_AGE = 60 * 60
        start_session(make_person("alice"), minutes_since_last_request=61, settings=settings)

        assert get_signed_in_users() == []

    def test_leaves_out_anonymous_sessions(self, settings):
        start_session(None, settings=settings)

        assert get_signed_in_users() == []

    def test_lists_a_user_with_several_sessions_once_with_their_latest_request(self, settings):
        alice = make_person("alice")
        start_session(alice, minutes_since_last_request=30, settings=settings)
        latest = start_session(alice, minutes_since_last_request=2, settings=settings)

        [row] = get_signed_in_users()

        assert row.session_count == 2
        assert abs(row.last_request - latest) < timedelta(seconds=1)

    def test_puts_the_most_recent_request_first(self, settings):
        start_session(make_person("earlier"), minutes_since_last_request=40, settings=settings)
        start_session(make_person("latest"), minutes_since_last_request=1, settings=settings)
        start_session(make_person("middle"), minutes_since_last_request=10, settings=settings)

        assert [row.user.username for row in get_signed_in_users()] == ["latest", "middle", "earlier"]

    def test_names_the_admin_who_logged_in_as_a_user(self, settings):
        admin = make_person("rdms_admin", is_superuser=True)
        alice = make_person("alice")
        start_session(alice, extra={"exit_users_pk": [[str(admin.pk), BACKEND]]}, settings=settings)

        assert summary(get_signed_in_users()) == [("alice", 1, ("rdms_admin",))]

    def test_skips_a_session_whose_user_has_been_deleted(self, settings):
        gone = make_person("gone")
        start_session(gone, settings=settings)
        gone.delete()

        assert get_signed_in_users() == []


@pytest.fixture
def superuser_client(client):
    client.force_login(make_person("rdms_admin", is_superuser=True))
    return client


@pytest.mark.django_db
@pytest.mark.urls("tufts_local.tests.urls")
class TestSignedInUsersPage:
    def test_lists_who_is_signed_in(self, superuser_client, settings):
        start_session(make_person("alice", first_name="Alice", last_name="Smith"), settings=settings)

        response = superuser_client.get("/signed-in-users/")

        assert response.status_code == 200
        page = response.content.decode()
        assert "alice" in page
        assert "Alice Smith" in page
        # the superuser looking is signed in too
        assert "rdms_admin" in page

    def test_says_who_logged_in_as_a_user(self, superuser_client, settings):
        admin = get_user_model().objects.get(username="rdms_admin")
        start_session(make_person("alice"), extra={"exit_users_pk": [[str(admin.pk), BACKEND]]}, settings=settings)

        page = superuser_client.get("/signed-in-users/").content.decode()

        assert "Logged in as them by rdms_admin" in page

    def test_staff_who_are_not_superusers_may_not(self, client):
        client.force_login(make_person("staff01", is_staff=True))

        assert client.get("/signed-in-users/").status_code == 302

    def test_anonymous_users_may_not(self, client):
        assert client.get("/signed-in-users/").status_code == 302
