# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from django.contrib.auth.decorators import login_required, user_passes_test
from django.template.response import TemplateResponse
from django.views.decorators.http import require_GET

from tufts_local.alerts import get_current_alerts
from tufts_local.views.task_report import _may_view


@login_required
@require_GET
@user_passes_test(_may_view)
def alert_list(request):
    """Every alert the registered sources currently raise, worst first, each with its detail and link."""
    alerts, checked_at = get_current_alerts()
    return TemplateResponse(
        request,
        'tufts_local/alert_list.html',
        # where an Acknowledge button comes back to
        {'alerts': alerts, 'generated_at': checked_at, 'return_path': request.get_full_path()},
    )


@login_required
@require_GET
@user_passes_test(_may_view)
def alert_indicator(request):
    """
    The alerts reduced to a count, marked in the worst one's colour, for the navbar.

    The only caller is coldfront-branding's common/authorized_navbar.html, which polls this
    with htmx on every page for staff.
    The alerts come from get_current_alerts(), so these polls don't each run every source.
    """
    alerts, checked_at = get_current_alerts()
    return TemplateResponse(
        request,
        'tufts_local/_alert_indicator.html',
        {
            'alerts': alerts,
            'critical_count': sum(alert.severity == 'danger' for alert in alerts),
            'generated_at': checked_at,
        },
    )
