# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Alerts for the people who review requests: allocation requests that have waited too long, and
project reviews waiting for someone to look at them.

Each goes to the holders of the permission ColdFront checks on the page it links to, so nobody
is alerted to work they couldn't open.
"""

import datetime

from django.template.defaultfilters import pluralize
from django.urls import reverse
from django.utils import timezone

from coldfront.core.allocation.models import Allocation
from coldfront.core.project.models import ProjectReview
from coldfront.core.utils.common import import_from_settings

from tufts_local.alerts import Alert, register_alert_source, with_permission

# how many days an allocation request may wait for review before it raises an alert, unless
# ALLOCATION_REQUEST_ALERT_DAYS says otherwise
DEFAULT_ALLOCATION_REQUEST_ALERT_DAYS = 1

# the statuses in which an allocation is waiting for a reviewer to approve or deny it. ColdFront's
# request list also shows Paid and Approved, but those are waiting on someone else.
AWAITING_REVIEW = ('New', 'Renewal Requested')


def allocation_request_alert_days():
    """ALLOCATION_REQUEST_ALERT_DAYS, read on every use, so a settings change needs no code change."""
    return import_from_settings('ALLOCATION_REQUEST_ALERT_DAYS', DEFAULT_ALLOCATION_REQUEST_ALERT_DAYS)


def waiting_since():
    """
    {allocation pk: when it entered its current status}, for each allocation awaiting review.

    The same date ColdFront's request list shows: for a renewal, when the renewal was requested,
    not when the allocation was created. Read from the allocations' history in one query, newest
    first, taking the earliest record of the unbroken run in the current status.
    """
    pending = Allocation.objects.filter(status__name__in=AWAITING_REVIEW).values_list('pk', 'status_id', 'created')
    # created stands in for an allocation with no history, such as one loaded without simple_history
    current = {pk: status_id for pk, status_id, _ in pending}
    since = {pk: created for pk, _, created in pending}
    history = (
        Allocation.history.filter(id__in=since)
        .order_by('id', '-history_date', '-history_id')
        .values_list('id', 'status_id', 'history_date')
    )
    run_ended = set()
    for pk, status_id, history_date in history:
        if pk in run_ended:
            continue
        if status_id == current[pk]:
            since[pk] = history_date
        else:
            run_ended.add(pk)
    return since


@register_alert_source('allocation requests', audience=with_permission('allocation.can_review_allocation_requests'))
def allocation_request_alerts():
    """A warning when allocation requests have waited longer than ALLOCATION_REQUEST_ALERT_DAYS."""
    days = allocation_request_alert_days()
    cutoff = timezone.now() - datetime.timedelta(days=days)
    # <=, so that with 0 days a request made this very moment counts too
    waiting = sorted(since for since in waiting_since().values() if since <= cutoff)
    if not waiting:
        return []
    count = len(waiting)
    oldest = (timezone.now() - waiting[0]).days
    if days:
        title = f'Allocation requests: {count} waiting more than {days} day{pluralize(days)}'
    else:
        title = f'Allocation requests: {count} waiting for review'
    return [
        Alert(
            'warning',
            title,
            detail=f'The oldest has waited {oldest} day{pluralize(oldest)}.',
            link=reverse('allocation-request-list'),
        )
    ]


@register_alert_source('project reviews', audience=with_permission('project.can_review_pending_project_reviews'))
def project_review_alerts():
    """Information when project reviews are waiting, however recent: there's no deadline to compare them to."""
    count = ProjectReview.objects.filter(status__name='Pending').count()
    if not count:
        return []
    return [
        Alert(
            'info',
            f'Project reviews: {count} pending',
            detail='PIs have submitted project reviews that nobody has marked complete.',
            link=reverse('project-review-list'),
        )
    ]
