# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.template.defaultfilters import pluralize
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django_q.models import Task

from tufts_local.alerts import forget_current_alerts
from tufts_local.models import TaskAcknowledgement
from tufts_local.task_failures import failures_between
from tufts_local.views.task_report import _may_view

logger = logging.getLogger(__name__)

# the report's filter and page, handed along on the POST so acting on a row doesn't
# throw away the view the person was looking at
RETURN_TO_PARAM = 'return_to'


def _ack_response(request, task):
    """
    What to send back after acknowledging or undoing.

    htmx gets the single cell it asked about, swapped in place. Anything else -- which
    means htmx didn't load, since the buttons are real forms -- gets sent back to the
    report with its filters and page intact.
    """
    # hand the filter/page back to the cell being swapped in, so the next click from it
    # still knows where the reader was
    return_to = request.POST.get(RETURN_TO_PARAM, '')
    if request.htmx:
        return TemplateResponse(request, 'tufts_local/_task_ack_cell.html', {'task': task, 'return_to': return_to})

    query = request.POST.get(RETURN_TO_PARAM, '').lstrip('?')
    report = reverse('task-report')
    return redirect(f'{report}?{query}' if query else report)


@login_required
@require_POST
@user_passes_test(_may_view)
def acknowledge_task(request, task_id):
    """
    Record that someone has seen a failed task, so it stops raising a failed-task alert.

    get_or_create rather than create: a double-click, a replayed POST and two people
    acking the same row at once should all leave one acknowledgement and no error. The
    first one to arrive is the one that gets the credit.
    """
    task = get_object_or_404(Task, id=task_id)
    if task.success:
        # nothing to acknowledge, and an acknowledged success would count as a failure
        # that has been dealt with in every query that goes looking for one
        messages.error(request, f'{task.name or task.id} did not fail, so there is nothing to acknowledge.')
        return _ack_response(request, task)

    _acknowledgement, created = TaskAcknowledgement.objects.get_or_create(
        task=task,
        defaults={'acknowledged_by': request.user},
    )
    if created:
        logger.info(f"Task '{task.name or task.id}' acknowledged by '{request.user.username}'.")
        forget_current_alerts()

    task.refresh_from_db()
    return _ack_response(request, task)


@login_required
@require_POST
@user_passes_test(_may_view)
def unacknowledge_task(request, task_id):
    """Undo an acknowledgement, so the failure raises an alert again. A no-op if there isn't one."""
    task = get_object_or_404(Task, id=task_id)
    deleted, _counts = TaskAcknowledgement.objects.filter(task=task).delete()
    if deleted:
        logger.info(f"Acknowledgement of task '{task.name or task.id}' undone by '{request.user.username}'.")
        forget_current_alerts()

    task.refresh_from_db()
    return _ack_response(request, task)


def _window_bound(request, name):
    """
    One end of the window to acknowledge, from the query string: an ISO datetime, or None if
    absent. Raises ValueError for anything else, so a mangled link acknowledges nothing.
    """
    value = request.GET.get(name)
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None or parsed.tzinfo is None:
        raise ValueError(f'{name} must be an ISO datetime with a time zone, not {value!r}')
    return parsed


@login_required
@require_POST
@user_passes_test(_may_view)
def acknowledge_tasks_in_bulk(request):
    """
    Acknowledge every unacknowledged failure in a window, optionally narrowed like the report.

    The window is in the query string, because it says which failures: `since` (inclusive) and
    `before` (exclusive). `before` is required, so a click acknowledges what its page or alert
    showed and never a failure that arrived afterwards. `name` and `group` narrow it the way the
    task report's filters do. The form body carries an optional `reason` and a `next` path to
    go back to.

    bulk_create with ignore_conflicts, for the same reason acknowledge_task uses get_or_create:
    a double click, or two people at once, leave one acknowledgement per task and no error.
    """
    try:
        since = _window_bound(request, 'since')
        before = _window_bound(request, 'before')
    except ValueError as e:
        return HttpResponseBadRequest(str(e))
    if before is None:
        return HttpResponseBadRequest('before is required')

    failures = failures_between(since=since, before=before)
    name = request.GET.get('name', '')
    group = request.GET.get('group', '')
    if name:
        failures = failures.filter(name__icontains=name)
    if group:
        failures = failures.filter(group=group)

    reason = request.POST.get('reason', '').strip()[: TaskAcknowledgement._meta.get_field('reason').max_length]
    # ids only: a backlog of failures would otherwise load every pickled argument and result
    task_ids = list(failures.values_list('id', flat=True))
    TaskAcknowledgement.objects.bulk_create(
        [TaskAcknowledgement(task_id=task_id, acknowledged_by=request.user, reason=reason) for task_id in task_ids],
        ignore_conflicts=True,
    )
    # bulk_create hands back every object, skipped ones included, so count what was asked for;
    # the query already left out acknowledged tasks, so only a simultaneous click differs
    count = len(task_ids)
    if count:
        logger.info(f"{count} failed task{pluralize(count)} acknowledged in bulk by '{request.user.username}'.")
        forget_current_alerts()
        messages.success(request, f'Acknowledged {count} failed task{pluralize(count)}.')
    else:
        messages.info(request, 'There were no unacknowledged failures to acknowledge.')

    next_path = request.POST.get('next', '')
    if not url_has_allowed_host_and_scheme(
        next_path, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_path = reverse('task-report')
    return redirect(next_path)
