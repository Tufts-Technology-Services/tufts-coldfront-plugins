# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from urllib.parse import urlsplit

from django.conf import settings
from django.shortcuts import resolve_url
from django_htmx.http import HttpResponseClientRefresh

REDIRECT_STATUSES = {301, 302, 303, 307, 308}


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
        if getattr(request, 'htmx', False) and redirects_to_login(response):
            return HttpResponseClientRefresh()
        return response


def redirects_to_login(response):
    if response.status_code not in REDIRECT_STATUSES:
        return False
    # Location is relative or absolute, with next= or not: compare paths
    return urlsplit(response['Location']).path == urlsplit(resolve_url(settings.LOGIN_URL)).path
