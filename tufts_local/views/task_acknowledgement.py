# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.http import require_POST
from django_q.models import Task

from tufts_local.models import TaskAcknowledgement
from tufts_local.views.task_report import _may_view, _wants_partial

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
    if _wants_partial(request):
        return TemplateResponse(request, 'tufts_local/_task_ack_cell.html', {'task': task, 'return_to': return_to})

    query = request.POST.get(RETURN_TO_PARAM, '').lstrip('?')
    report = reverse('task-report')
    return redirect(f'{report}?{query}' if query else report)


@login_required
@require_POST
@user_passes_test(_may_view)
def acknowledge_task(request, task_id):
    """
    Record that someone has seen a failed task, so it stops lighting the indicator.

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

    task.refresh_from_db()
    return _ack_response(request, task)


@login_required
@require_POST
@user_passes_test(_may_view)
def unacknowledge_task(request, task_id):
    """Undo an acknowledgement, lighting the indicator again. A no-op if there isn't one."""
    task = get_object_or_404(Task, id=task_id)
    deleted, _counts = TaskAcknowledgement.objects.filter(task=task).delete()
    if deleted:
        logger.info(f"Acknowledgement of task '{task.name or task.id}' undone by '{request.user.username}'.")

    task.refresh_from_db()
    return _ack_response(request, task)
