# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from django.conf import settings
from django.contrib.auth.decorators import login_required, user_passes_test
from django.template.response import TemplateResponse
from django.views.decorators.http import require_GET

from tufts_local.signed_in_users import get_signed_in_users


def _is_superuser(user):
    return user.is_superuser


@login_required
@require_GET
@user_passes_test(_is_superuser)
def signed_in_users(request):
    """Superusers' list of who is signed in now, e.g. to check before a deploy."""
    return TemplateResponse(
        request,
        "tufts_local/signed_in_users.html",
        {
            "signed_in_users": get_signed_in_users(),
            "timeout_minutes": settings.SESSION_COOKIE_AGE // 60,
        },
    )
