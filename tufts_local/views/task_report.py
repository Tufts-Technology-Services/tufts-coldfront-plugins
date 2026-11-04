# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import ast
import datetime
import logging
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Count, ExpressionWrapper, F, Q, TextField
from django.template.defaultfilters import pluralize
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from django_q.models import OrmQ, Schedule, Task
from picklefield.fields import PickledObject

from tufts_local.forms import TaskFilterForm
from tufts_local.queue_health import get_task_queue_health
from tufts_local.task_failures import (
    exclude_ignored,
    failures_between,
    ignore_rules,
    is_ignored,
    recent_window_hours,
)

logger = logging.getLogger(__name__)

TASKS_PER_PAGE = 50

# The three outcomes a finished task can be in, as (aggregate name, label), for the counts at
# the top of the Completed section. django-q records only a boolean, so 'Acknowledged' is this
# app's own third state: a failure someone has signed off (models.TaskAcknowledgement). Counting
# it apart keeps 'Failed' meaning what the task failure alerts count -- failures nobody has dealt
# with yet -- so the counts explain the alerts instead of contradicting them.
STATUS_LABELS = (('succeeded', 'Succeeded'), ('failed', 'Failed'), ('acknowledged', 'Acknowledged'))
# how often the page polls itself for fresh rows; the template reads this too
REFRESH_SECONDS = 5

# The state filter (TaskFilterForm.state) covers both tables. A pending state keeps those rows
# of the Pending table and empties Completed; a completed state does the reverse.
PENDING_STATES = {'scheduled': 'Scheduled', 'queued': 'Queued'}
COMPLETED_STATES = {
    'success': Q(success=True),
    'failed': Q(success=False),
    'unacknowledged': Q(success=False, acknowledgement__isnull=True),
    'acknowledged': Q(success=False, acknowledgement__isnull=False),
}


def _parse_kwargs(raw):
    """
    Parse a Schedule.kwargs string, or return None if neither syntax applies.

    Mirrors django_q.scheduler, which accepts two spellings in that TextField: a dict
    repr ("{'q_options': {...}}", what schedule() writes) and bare keyword arguments
    ("timeout=600", what the admin form and hand-made schedules use). Only supporting
    the first meant every schedule written the second way logged a parse warning.
    """
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        pass
    try:
        keywords = ast.parse(f'f({raw})').body[0].value.keywords
        return {kw.arg: ast.literal_eval(kw.value) for kw in keywords}
    except (ValueError, SyntaxError, AttributeError, IndexError):
        return None


def _q_options(schedule):
    """
    Return the q_options dict a schedule was created with.

    django_q's Schedule model has no name/group columns of its own: anything passed as
    q_options to schedule() is stored inside the kwargs TextField, and parsed back out
    when the scheduler runs it.
    """
    if not schedule.kwargs:
        return {}
    kwargs = _parse_kwargs(schedule.kwargs)
    if kwargs is None:
        logger.debug(f'Could not parse kwargs for schedule {schedule.id}: {schedule.kwargs}')
        return {}
    options = kwargs.get('q_options', {}) if isinstance(kwargs, dict) else {}
    return options if isinstance(options, dict) else {}


def _last_run_statuses(schedules):
    """Map schedule.task (last spawned task id) -> that Task, in a single query."""
    task_ids = [s.task for s in schedules if s.task]
    if not task_ids:
        return {}
    return {t.id: t for t in Task.objects.filter(id__in=task_ids)}


def _scheduled_rows():
    # repeats=0 means the schedule is exhausted; the scheduler skips those too
    schedules = list(Schedule.objects.exclude(repeats=0))
    last_runs = _last_run_statuses(schedules)
    rows = []
    for schedule in schedules:
        options = _q_options(schedule)
        row_name = schedule.name or options.get('task_name') or ''
        row_group = options.get('group') or ''
        last_run = last_runs.get(schedule.task)
        rows.append(
            {
                'kind': 'Scheduled',
                'name': row_name,
                'group': row_group,
                'func': schedule.func,
                'when': schedule.next_run,
                'detail': schedule.get_schedule_type_display(),
                'repeats': schedule.repeats,
                'last_run': last_run,
            }
        )
    return rows


def _queued_rows():
    """
    Rows for tasks sitting in the broker queue.

    Only populated when Q_CLUSTER uses the ORM broker; on Redis this table is empty.
    Each row's name/group/func come from the signed payload, which can fail to unpack.
    """
    rows = []
    for item in OrmQ.objects.all().order_by('-id'):
        try:
            # a payload that won't unsign yields an id of '*<ExceptionName>' and empty everything else
            row_name = item.name() or ''
            row_group = item.group() or ''
            func = item.func() or 'unreadable payload'
        except Exception as e:
            logger.warning(f'Could not read payload for queued task {item.id}: {e}')
            row_name, row_group, func = '', '', 'unreadable payload'
        rows.append(
            {
                'kind': 'Queued',
                'name': row_name,
                'group': row_group,
                'func': func,
                'when': None,
                'detail': 'Locked by a cluster' if item.lock else 'Waiting',
                'repeats': None,
                'last_run': None,
            }
        )
    return rows


def _filter_rows(rows, name, state):
    """
    Narrow pending rows in Python.

    A schedule's name lives inside a repr in a TextField and a queued task's inside a
    signed payload, so neither can be filtered in SQL.
    """
    if name:
        rows = [row for row in rows if name.lower() in row['name'].lower()]
    if state:
        rows = [row for row in rows if row['kind'] == PENDING_STATES.get(state)]
    return rows


def _filter_tasks(tasks, name, state):
    """Narrow a queryset of finished tasks the way the report's filters ask."""
    if name:
        tasks = tasks.filter(name__icontains=name)
    if state in PENDING_STATES:
        return tasks.none()
    if state in COMPLETED_STATES:
        tasks = tasks.filter(COMPLETED_STATES[state])
    return tasks


def _status_counts(tasks):
    """
    How many of these tasks ended each way, counted in the database.

    Every status is always present, including at zero: 'Failed 0' is the answer someone
    scanning the counts is looking for, and a missing column doesn't give it to them.
    """
    counts = tasks.aggregate(
        succeeded=Count('id', filter=Q(success=True)),
        failed=Count('id', filter=Q(success=False, acknowledgement__isnull=True)),
        acknowledged=Count('id', filter=Q(success=False, acknowledgement__isnull=False)),
    )
    return [{'status': label, 'count': counts[key]} for key, label in STATUS_LABELS]


def _count_periods(tasks, now):
    """
    The counts table: the tasks split at one clock reading, recent_window_hours() back.

    Split at a single instant, the two periods partition the tasks exactly: none lands in both
    and none in neither. The recent one is the window the critical failure alert watches.
    """
    hours = recent_window_hours()
    boundary = now - datetime.timedelta(hours=hours)
    periods = []
    for label, since, until, period_tasks in (
        (f'Last {hours} hour{pluralize(hours)}', boundary, None, tasks.filter(stopped__gte=boundary)),
        ('Earlier', None, boundary, tasks.filter(stopped__lt=boundary)),
    ):
        counts = _status_counts(period_tasks)
        periods.append(
            {
                'label': label,
                'since': since,
                'until': until,
                'counts': counts,
                'total': sum(count['count'] for count in counts),
            }
        )
    return periods


# Task's pickled columns, read raw and unpickled here rather than by the field (see _unpickled)
PICKLED_FIELDS = ('args', 'kwargs', 'result')


class UnreadableValue:
    """
    Stands in for a pickled value this process can't load, such as a result holding an
    instance of a class that has since been renamed or removed. Renders as a short note
    saying so, in the Message column and the detail modal alike.
    """

    def __init__(self, error):
        self.error = error

    def __str__(self):
        return f'<unreadable: {type(self.error).__name__}: {self.error}>'


def _with_raw_pickles(tasks):
    """
    Fetch the pickled columns as the text stored in the database, instead of unpickled.

    picklefield swallows a failure to unpickle a value it loads and hands back that stored
    base64 text in its place, so an unloadable result would be shown as a blob of base64
    with nothing to say why. Wrapping the column in a text-typed expression skips the
    field's converter; _unpickled then decodes it where the error can be seen. The columns
    are deferred so each one is fetched once, as text, and not a second time pickled.
    """
    raw = {f'raw_{name}': ExpressionWrapper(F(name), output_field=TextField()) for name in PICKLED_FIELDS}
    return tasks.defer(*PICKLED_FIELDS).annotate(**raw)


def _unpickled(task, name):
    """
    One of a task's pickled columns, decoded, or an UnreadableValue saying why it can't be.

    Decoded by the field itself, so its compression and wrapping settings apply. Handing it
    a PickledObject is how picklefield is told the text is certainly a pickle, which makes
    it raise on failure instead of returning the text.
    """
    raw = getattr(task, f'raw_{name}')
    if raw is None:
        return None
    try:
        return Task._meta.get_field(name).to_python(PickledObject(raw))
    except Exception as e:
        logger.warning(f'Could not unpickle {name} of task {task.id}: {type(e).__name__}: {e}')
        return UnreadableValue(e)


def _result_message(result):
    """
    The one line the Completed table shows for a task's result.

    The plugins' tasks return a dict carrying a 'message', which is the useful part; anything
    else -- a failure's traceback, a bare string, some other structure -- is shown as text.
    The whole result is in the row's detail modal either way.
    """
    if isinstance(result, dict) and result.get('message') is not None:
        return str(result['message'])
    return '' if result is None else str(result)


def _may_view(user):
    """
    Who the task pages are for: superusers and staff alike.

    Defined once, here, because the acknowledgement views share it, and the report links to
    them -- a link its audience can't follow is worse than none.
    """
    return user.is_superuser or user.is_staff


@login_required
@require_GET
@user_passes_test(_may_view)
def task_report(request):
    """
    View of django-q2 tasks: what is still pending (scheduled or queued) and what has
    already run, with its success/failure status and result.
    """
    # tasks matching an admin-maintained IgnoredTask rule are dropped everywhere on this page
    ignored_names, ignored_groups = ignore_rules()
    all_pending = [
        row
        for row in _scheduled_rows() + _queued_rows()
        if not is_ignored(row['name'], row['group'], ignored_names, ignored_groups)
    ]

    form = TaskFilterForm(request.GET or None)

    name = ''
    state = ''
    if form.is_bound and form.is_valid():
        name = form.cleaned_data['name']
        state = form.cleaned_data['state']

    pending_rows = _filter_rows(all_pending, name, state)
    # queued rows have no scheduled time, so they sort ahead of the soonest scheduled run
    pending_rows.sort(key=lambda row: (row['when'] is not None, row['when']))

    tasks = _filter_tasks(exclude_ignored(Task.objects.all(), ignored_names, ignored_groups), name, state)
    now = timezone.now()
    # counted from the same filtered tasks the table lists, so the two always agree
    periods = _count_periods(tasks, now)
    # every row asks whether it has been acknowledged; without this that is 50 extra
    # queries a page, every five seconds
    tasks = _with_raw_pickles(tasks.select_related('acknowledgement').order_by('-stopped'))

    paginator = Paginator(tasks, TASKS_PER_PAGE)
    try:
        page_obj = paginator.page(request.GET.get('page'))
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)
    # a list, so the summary set on each task here is the one the template reads
    page_obj.object_list = list(page_obj.object_list)
    for task in page_obj.object_list:
        for field_name in PICKLED_FIELDS:
            setattr(task, field_name, _unpickled(task, field_name))
        task.result_message = _result_message(task.result)

    filter_parameters = urlencode({k: v for k, v in (('name', name), ('state', state)) if v})

    # htmx polls this same view and swaps in just the tables, leaving the filter inputs alone
    template = '_task_tables.html' if request.htmx else 'task_report.html'

    # "Acknowledge all shown": the unacknowledged failures matching the filters, as of this page
    # load. It sits outside the polled tables, so `before` keeps it to what was on screen.
    # The state needn't go along: any state that leaves an unacknowledged failure on screen
    # leaves all of them, and the button isn't offered under the others.
    unacknowledged = _filter_tasks(failures_between(before=now), name, state)
    window = {'before': now.isoformat(), **({'name': name} if name else {})}

    return TemplateResponse(
        request,
        f'tufts_local/{template}',
        {
            'form': form,
            'pending_rows': pending_rows,
            'page_obj': page_obj,
            'is_paginated': paginator.num_pages > 1,
            'task_count': paginator.count,
            'filter_parameters': f'{filter_parameters}&' if filter_parameters else '',
            # where an Ack button should send someone back to, for the case where htmx
            # isn't there to swap the cell in place
            'return_to': f'{filter_parameters}&page={page_obj.number}'
            if filter_parameters
            else f'page={page_obj.number}',
            'refresh_seconds': REFRESH_SECONDS,
            'unacknowledged_count': 0 if request.htmx else unacknowledged.count(),
            'acknowledge_all_url': f'{reverse("task-acknowledge-bulk")}?{urlencode(window)}',
            'return_path': request.get_full_path(),
            # checked on every poll too: the status rides back with the tables, out-of-band
            'queue_health': get_task_queue_health(),
            'poll': bool(request.htmx),
            'periods': periods,
            'status_labels': [label for _key, label in STATUS_LABELS],
        },
    )
