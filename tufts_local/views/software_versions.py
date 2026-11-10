# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from django.contrib.auth.decorators import login_required, user_passes_test
from django.template.response import TemplateResponse
from django.views.decorators.http import require_GET

from tufts_local.software_versions import get_software_versions


def _is_staff_or_superuser(user):
    return user.is_staff or user.is_superuser


@login_required
@require_GET
@user_passes_test(_is_staff_or_superuser)
def software_versions(request):
    """Staff's page of the ColdFront version, Python and Django's, and the installed packages."""
    return TemplateResponse(request, "tufts_local/software_versions.html", {"versions": get_software_versions()})
