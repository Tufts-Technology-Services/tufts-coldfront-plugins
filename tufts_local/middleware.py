# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponseForbidden
from django.shortcuts import resolve_url
from django.urls import Resolver404, resolve
from django_htmx.http import HttpResponseClientRefresh

from tufts_local.su import DEFAULT_READ_ONLY_ALLOWED_POSTS, SU_READ_ONLY_SESSION_KEY

REDIRECT_STATUSES = {301, 302, 303, 307, 308}

# what a read-only "View as" session may do
READ_ONLY_SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


class HtmxLoginRefreshMiddleware:
    """
    Turn a redirect to the login page, in answer to an htmx request, into a refresh of the whole page.

    The browser follows a redirect inside an htmx request without htmx seeing it, so once a session
    has expired, a polled fragment (the navbar's task indicator, every 30s) gets the login page and
    htmx swaps it into the page. A refresh instead reloads the page the user is on, whose own redirect
    to login carries next=<that page>, so they land back on it after logging in.

    Only redirects to LOGIN_URL change: any other redirect may be what the htmx request wants.
    django-htmx has no equivalent; https://github.com/adamchainz/django-htmx/issues/91 has been open
    since 2021. Goes after django_htmx's HtmxMiddleware, which sets request.htmx: appending it to
    coldfront's MIDDLEWARE does that.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # getattr: a misordered MIDDLEWARE would otherwise fail every request, not just this one's
        if getattr(request, "htmx", False) and redirects_to_login(response):
            return HttpResponseClientRefresh()
        return response


def redirects_to_login(response):
    if response.status_code not in REDIRECT_STATUSES:
        return False
    # Location is relative or absolute, with next= or not: compare paths
    return urlsplit(response["Location"]).path == urlsplit(resolve_url(settings.LOGIN_URL)).path


class SuReadOnlyMiddleware:
    """
    Refuse anything but a read, while a superuser is viewing as another user.

    A "View as" session (see tufts_local.views.view_as) is for looking at a page the way someone
    else sees it, so while its session flag is set, a request that isn't GET, HEAD or OPTIONS gets
    a 403. The exceptions are views whose POST only shows something, named by URL in the setting
    VIEW_AS_READ_ONLY_ALLOWED_POSTS (tufts_local.su.DEFAULT_READ_ONLY_ALLOWED_POSTS where it isn't
    set), and signing out, so the session can always be ended. Unlisted is refused: a view nobody
    thought about is assumed to write. Put it after SessionMiddleware.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        session = getattr(request, "session", None)
        if (
            session is not None
            and session.get(SU_READ_ONLY_SESSION_KEY)
            and request.method not in READ_ONLY_SAFE_METHODS
            and not self.is_allowed(request)
        ):
            return HttpResponseForbidden(
                "This is a read-only view: you are viewing as another user, so nothing can be changed. "
                "Release them to make changes."
            )
        return self.get_response(request)

    @staticmethod
    def is_allowed(request):
        """Whether a read-only session may POST here: signing out, or a view on the allowlist."""
        try:
            view_name = resolve(request.path_info).view_name
        except Resolver404:
            return False
        allowed = getattr(settings, "VIEW_AS_READ_ONLY_ALLOWED_POSTS", DEFAULT_READ_ONLY_ALLOWED_POSTS)
        return view_name == "logout" or view_name in allowed
