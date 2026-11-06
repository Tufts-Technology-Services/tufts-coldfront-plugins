# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Alerts about coldfront_billing: requested no-cost quota allotments that could not be applied.

When an allocation is activated, or a change to it approved, billing applies the no-cost quota the
requester asked for. If it can't, it lets the allocation through and records a
NoCostQuotaAllotmentFailure instead; this turns the unresolved ones into alerts.

Like billing_tasks, this module touches coldfront_billing, so LocalConfig.ready() imports it only
when billing is installed, and billing itself knows nothing of these alerts.
"""

from coldfront_billing.models import NoCostQuotaAllotmentFailure
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode

from tufts_local.alerts import Alert, register_alert_source, superusers


def unresolved_allotment_failures():
    return (
        NoCostQuotaAllotmentFailure.objects.filter(resolved_at__isnull=True)
        .select_related("allocation__project", "no_cost_quota__user")
        .order_by("created")
    )


@register_alert_source("no-cost quota allotments", audience=superusers)
def allotment_failure_alerts():
    """
    A warning for each failure nobody has resolved, oldest first.

    Superusers only: the link goes to billing's assign page, which only a superuser can use for
    someone else's quota. Each clears by itself once an allotment from that quota is saved for the
    allocation, or when acknowledged here.
    """
    return [_alert_for(failure) for failure in unresolved_allotment_failures()]


def _alert_for(failure):
    allocation = failure.allocation
    quota = failure.no_cost_quota
    if quota is not None:
        # where to apply it by hand
        link = f"{reverse('assign_no_cost_quota')}?{urlencode({'selected_quota': quota.pk})}"
        requested = f"{quota.user.username}'s {quota.quota_type} quota"
    else:
        link = reverse("allocation-detail", kwargs={"pk": allocation.pk})
        requested = "a no-cost quota"
    failed_on = timezone.localtime(failure.created).strftime("%Y-%m-%d")
    return Alert(
        "warning",
        f"No-cost quota not applied: {allocation.project.title}, allocation {allocation.pk}",
        detail=f"The request for {requested} failed on {failed_on}: {failure.message}",
        link=link,
        ack_url=reverse("allotment-failure-acknowledge", kwargs={"pk": failure.pk}),
    )
