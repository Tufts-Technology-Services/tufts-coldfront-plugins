# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
django-su, with a record of who signs in as whom.

Set SU_CUSTOM_LOGIN_ACTION to "tufts_local.su.log_su_login". django-su calls it both when an
administrator signs in as someone and when they release them, for every route: the "Login as..."
page and the View as control alike.
"""

import logging

from django.contrib.auth import login
from django.urls import Resolver404, resolve

logger = logging.getLogger(__name__)

# the pages that release a user: django-su's, and the View as control's, which calls it
SU_END_URL_NAMES = ("su_logout", "view-as-exit")


def describe_su_change(request, user):
    """
    One line saying who signed in as whom, or released whom, and from where.

    `user` is django-su's: the user being signed in as when it starts, or the user being returned
    to when it ends. The two are told apart by the page: su_logout, or the View as control's exit,
    is the end. The administrator is the real one, the first user django-su remembers, so signing in
    as a second account from the first still names the first. The mode is "read-only" where the request was marked
    `su_read_only`, and "full" otherwise. Where the request came from is X-Forwarded-For, which
    nginx appends to and a client can set, and the address nginx saw, each as given, quoted.
    """
    # imported here, not at the top: tufts_local.middleware imports this module, and production loads
    # that for its other middleware, which must keep working with a coldfront-utils older than 0.2.0
    from coldfront_utils.task_context.su import signed_in_by

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


# The session flag of a "View as" session: while it is set, the user may look but not change anything.
# It is set after django-su signs in as the user, because login() empties the session when the user
# changes, and cleared by the same call when they are released.
SU_READ_ONLY_SESSION_KEY = "su_read_only"


# The views a read-only session may still POST to: ones whose POST only shows something, so a flow can
# be followed to the step that would change data. By URL name, namespaced ("app:name") where the URLs
# are. The setting VIEW_AS_READ_ONLY_ALLOWED_POSTS replaces this list, so a plugin's display-only POST
# can be let through from settings, without a code change. Signing out is always let through.
DEFAULT_READ_ONLY_ALLOWED_POSTS = (
    # the add-users flow: the search results are a POST that only lists matches
    "project-add-users-search-results",
)
