# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from django.http import Http404
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST
from django_su.views import login_as_user, su_logout

from tufts_local.su import SU_READ_ONLY_SESSION_KEY


def _page_to_return_to(request, given):
    """`given` if it is a page on this site, and the home page if it is anything else."""
    if given and url_has_allowed_host_and_scheme(
        given, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return given
    return "/"


@require_POST
def view_as_start(request):
    """
    Sign in as another user, read-only, and go back to the page the superuser was on.

    Takes `user`, the pk of the user to view as, and `next`, the page to return to. It is
    django-su's login_as_user, which always redirects to one fixed page, with the request marked
    read-only so django-su's logging action says so. Only superusers may: that is django-su's own
    rule, and when it refuses, nobody is signed in as anyone and no flag is set.
    """
    try:
        user_pk = int(request.POST.get("user", ""))
    except ValueError:
        raise Http404("No such user") from None
    request.su_read_only = True
    login_as_user(request, user_pk)
    if request.user.pk != user_pk:
        # refused: login_as_user redirects to the login page rather than signing anyone in
        return redirect("/")
    # after, not before: login() empties the session when the user changes
    request.session[SU_READ_ONLY_SESSION_KEY] = True
    return redirect(_page_to_return_to(request, request.POST.get("next")))


@require_GET
def view_as_exit(request):
    """Release the user viewed as, and go back to the page in `next`."""
    response = su_logout(request)
    if response.status_code == 400:
        # nobody to release: django-su says so
        return response
    return redirect(_page_to_return_to(request, request.GET.get("next")))
