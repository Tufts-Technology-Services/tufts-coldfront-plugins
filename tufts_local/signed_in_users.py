# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Who is signed in to ColdFront, read from the sessions.

ColdFront saves the session on every request (SESSION_SAVE_EVERY_REQUEST), which moves its expiry to
SESSION_COOKIE_AGE from then: an hour unless SESSION_INACTIVITY_TIMEOUT says otherwise. So a session
that hasn't expired is someone who made a request within that time and hasn't signed out, and their
last request was SESSION_COOKIE_AGE before the expiry. Closing the browser drops the cookie but not
the session, so someone who closed it still shows until the session expires.

Reads the database's sessions, so it needs a database-backed SESSION_ENGINE (Tufts uses cached_db).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.utils import timezone

# where django-su keeps the users to go back to, the admin who logged in as someone first
SU_EXIT_USERS_KEY = "exit_users_pk"


@dataclass(frozen=True)
class SignedInUser:
    """A user with at least one session that hasn't expired."""

    user: User
    last_request: datetime
    # how many unexpired sessions they have, e.g. one per browser
    session_count: int
    # the admins who logged in as this user with django-su, by username
    logged_in_as_by: tuple[str, ...] = ()


def get_signed_in_users() -> list[SignedInUser]:
    """Everyone with an unexpired session, the most recent request first."""
    age = timedelta(seconds=settings.SESSION_COOKIE_AGE)
    expiries = {}
    counts = {}
    su_admin_pks = {}
    for session in Session.objects.filter(expire_date__gt=timezone.now()):
        data = session.get_decoded()
        user_pk = data.get(SESSION_KEY)
        if user_pk is None:
            continue
        user_pk = str(user_pk)
        expiries[user_pk] = max(expiries.get(user_pk, session.expire_date), session.expire_date)
        counts[user_pk] = counts.get(user_pk, 0) + 1
        exit_users = data.get(SU_EXIT_USERS_KEY) or []
        if exit_users:
            su_admin_pks.setdefault(user_pk, set()).add(str(exit_users[0][0]))

    all_pks = set(expiries) | {pk for pks in su_admin_pks.values() for pk in pks}
    users = {str(user.pk): user for user in get_user_model().objects.filter(pk__in=all_pks)}
    rows = [
        SignedInUser(
            user=users[user_pk],
            last_request=expiry - age,
            session_count=counts[user_pk],
            logged_in_as_by=tuple(sorted(users[pk].username for pk in su_admin_pks.get(user_pk, ()) if pk in users)),
        )
        for user_pk, expiry in expiries.items()
        if user_pk in users
    ]
    return sorted(rows, key=lambda row: row.last_request, reverse=True)
