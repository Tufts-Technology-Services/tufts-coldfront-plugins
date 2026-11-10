# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
django-su, with a record of who signs in as whom.

Set SU_CUSTOM_LOGIN_ACTION to "tufts_local.su.log_su_login". django-su calls it both when an
administrator signs in as someone and when they release them.
"""

import logging

from coldfront_utils.task_context.su import signed_in_by
from django.contrib.auth import login
from django.urls import Resolver404, resolve

logger = logging.getLogger(__name__)

# the pages that release a user
SU_END_URL_NAMES = ("su_logout",)


def describe_su_change(request, user):
    """
    One line saying who signed in as whom, or released whom, and from where.

    `user` is django-su's: the user being signed in as when it starts, or the user being returned
    to when it ends. The two are told apart by the page: su_logout is the end. The administrator is the real one, the first user django-su remembers, so signing in
    as a second account from the first still names the first. The mode is "read-only" where the request was marked
    `su_read_only`, and "full" otherwise. Where the request came from is X-Forwarded-For, which
    nginx appends to and a client can set, and the address nginx saw, each as given, quoted.
    """
    try:
        ending = resolve(request.path_info).url_name in SU_END_URL_NAMES
    except Resolver404:
        ending = False
    # repr() quotes them and escapes a line break, so a header can't start another log line
    origin = (
        f"forwarded_for={request.META.get('HTTP_X_FORWARDED_FOR')!r} remote_addr={request.META.get('REMOTE_ADDR')!r}"
    )
    if ending:
        # releasing the last user empties django-su's list before this runs, so the administrator is
        # then who they go back to
        administrator = signed_in_by(request) or user
        return (
            f"su end: admin={administrator.get_username()} released={request.user.get_username()} "
            f"back_to={user.get_username()} {origin}"
        )
    administrator = signed_in_by(request) or request.user
    mode = "read-only" if getattr(request, "su_read_only", False) else "full"
    return f"su start: admin={administrator.get_username()} target={user.get_username()} mode={mode} {origin}"


def log_su_login(request, user):
    """
    django-su's SU_CUSTOM_LOGIN_ACTION: log the change, then sign in as `user`.

    It replaces django-su's own call to login(), so it makes that call itself, and makes it even
    when the logging fails: an administrator who can't release a user because a log line broke
    would be stuck signed in as them.
    """
    try:
        logger.info("%s", describe_su_change(request, user))
    except Exception:
        logger.exception("Couldn't log a change of who is signed in as whom")
    login(request, user)
