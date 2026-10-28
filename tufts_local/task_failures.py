# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Which finished django-q tasks still need someone's attention, and the alerts that say so.

A failure needs attention until someone acknowledges it on the task report
(models.TaskAcknowledgement), unless it matches the admin-maintained ignore list
(models.IgnoredTask). The task pages and the alerts list share these rules, so the
counts on the task summary page explain the alert rather than contradict it.
"""

import datetime

from django.template.defaultfilters import pluralize
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django_q.models import Task

from coldfront.core.utils.common import import_from_settings

from tufts_local.alerts import Alert, register_alert_source
from tufts_local.models import IgnoredTask

# how many hours back counts as recent, unless TASK_RECENT_WINDOW_HOURS says otherwise: failures
# this recent raise the critical alert, and the task summary lists these tasks in full
DEFAULT_RECENT_WINDOW_HOURS = 24

# how far back the warning alert looks for older failures, unless TASK_FAILURE_ALERT_DAYS says
# otherwise. Without a limit, failures nobody will ever answer -- such as those left over from
# a migration -- would keep the alert up forever. Older ones are still on the task report.
DEFAULT_FAILURE_ALERT_DAYS = 7


def ignore_rules():
    """
    The admin-maintained ignore list, as (name prefixes, group ids).

    Blank patterns are dropped: a '' prefix would match every task and silently empty
    the whole report.
    """
    names = []
    groups = []
    for rule in IgnoredTask.objects.all():
        pattern = (rule.pattern or '').strip()
        if not pattern:
            continue
        if rule.field == IgnoredTask.GROUP:
            groups.append(pattern)
        else:
            names.append(pattern)
    return names, groups


def is_ignored(name, group, ignored_names, ignored_groups):
    name = (name or '').lower()
    group = (group or '').lower()
    if group and any(group == pattern.lower() for pattern in ignored_groups):
        return True
    return bool(name) and any(name.startswith(pattern.lower()) for pattern in ignored_names)


def exclude_ignored(tasks, ignored_names, ignored_groups):
    for pattern in ignored_names:
        tasks = tasks.exclude(name__istartswith=pattern)
    for pattern in ignored_groups:
        tasks = tasks.exclude(group__iexact=pattern)
    return tasks


def unacknowledged_failures():
    """Failed tasks nobody has acknowledged, less the ignored ones."""
    tasks = exclude_ignored(Task.objects.all(), *ignore_rules())
    return tasks.filter(success=False, acknowledgement__isnull=True)


def failures_between(since=None, before=None):
    """Unacknowledged failures that finished at or after `since` and before `before`, either open."""
    failures = unacknowledged_failures()
    if since is not None:
        failures = failures.filter(stopped__gte=since)
    if before is not None:
        failures = failures.filter(stopped__lt=before)
    return failures


def _ack_url(since, before):
    """Where an alert's Acknowledge button posts: the bulk acknowledgement, for exactly its window."""
    window = {key: value.isoformat() for key, value in (('since', since), ('before', before)) if value is not None}
    return f'{reverse("task-acknowledge-bulk")}?{urlencode(window)}'


def recent_window_hours():
    """TASK_RECENT_WINDOW_HOURS, read on every use, so a settings change needs no code change."""
    return import_from_settings('TASK_RECENT_WINDOW_HOURS', DEFAULT_RECENT_WINDOW_HOURS)


def failure_alert_days():
    """
    TASK_FAILURE_ALERT_DAYS: how many days back the warning alert counts failures, or None
    for no limit. Read on every check, so a settings change needs no code change.
    """
    return import_from_settings('TASK_FAILURE_ALERT_DAYS', DEFAULT_FAILURE_ALERT_DAYS)


@register_alert_source('background tasks')
def task_failure_alerts():
    """
    Critical for failures in the recent window (TASK_RECENT_WINDOW_HOURS), a warning for
    unanswered ones before that, back as far as TASK_FAILURE_ALERT_DAYS.

    Counts only, never a task's name, arguments or result: those can hold anything a task was
    given or returned, and the alerts list is no place for it. The detail is on the task report.
    """
    now = timezone.now()
    hours = recent_window_hours()
    recent_start = now - datetime.timedelta(hours=hours)
    days = failure_alert_days()
    earliest = now - datetime.timedelta(days=days) if days else None
    link = reverse('task-report')

    # Each alert's Acknowledge button covers the window it counted, bounded by this moment, so
    # it acknowledges what the alert said and not a failure that arrived after it was checked.
    alerts = []
    recent = failures_between(since=recent_start, before=now).count()
    if recent:
        alerts.append(
            Alert(
                'danger',
                f'Background tasks: {recent} failed in the last {hours} hour{pluralize(hours)}',
                detail='Acknowledge them here, or one at a time on the task report, once they have been dealt with.',
                link=link,
                ack_url=_ack_url(recent_start, now),
            )
        )

    earlier = failures_between(since=earliest, before=recent_start).count()
    if earlier:
        window = f' and within the last {days} day{pluralize(days)}' if days else ''
        alerts.append(
            Alert(
                'warning',
                f'Background tasks: {earlier} earlier failure{pluralize(earlier)} not acknowledged',
                detail=f'Failed more than {hours} hour{pluralize(hours)} ago{window}. '
                'Acknowledge them here, or one at a time on the task report.',
                link=link,
                ack_url=_ack_url(earliest, recent_start),
            )
        )
    return alerts
