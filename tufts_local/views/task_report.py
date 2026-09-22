# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import ast
import logging
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.template.response import TemplateResponse
from django.views.decorators.http import require_GET
from django_q.models import OrmQ, Schedule, Task

from tufts_local.forms import TaskFilterForm
from tufts_local.models import IgnoredTask

logger = logging.getLogger(__name__)

TASKS_PER_PAGE = 50
# how often the page polls itself for fresh rows; the template reads this too
REFRESH_SECONDS = 5


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


def _filter_rows(rows, name, group):
    """
    Narrow pending rows in Python.

    A schedule's name and group live inside a repr in a TextField and a queued task's
    inside a signed payload, so neither can be filtered in SQL.
    """
    if name:
        rows = [row for row in rows if name.lower() in row['name'].lower()]
    if group:
        rows = [row for row in rows if row['group'] == group]
    return rows


def _ignore_rules():
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


def _is_ignored(name, group, ignored_names, ignored_groups):
    name = (name or '').lower()
    group = (group or '').lower()
    if group and any(group == pattern.lower() for pattern in ignored_groups):
        return True
    return bool(name) and any(name.startswith(pattern.lower()) for pattern in ignored_names)


def _exclude_ignored(tasks, ignored_names, ignored_groups):
    for pattern in ignored_names:
        tasks = tasks.exclude(name__istartswith=pattern)
    for pattern in ignored_groups:
        tasks = tasks.exclude(group__iexact=pattern)
    return tasks


def _wants_partial(request):
    """
    True when htmx is asking for just the tables rather than the whole page.

    request.htmx is set by django_htmx's HtmxMiddleware; fall back to the header it
    reads so the refresh still works if that middleware isn't in the stack.
    """
    htmx = getattr(request, 'htmx', None)
    if htmx is not None:
        return bool(htmx)
    return request.headers.get('HX-Request') == 'true'


def _group_choices(pending_rows, ignored_names, ignored_groups):
    tasks = _exclude_ignored(Task.objects.all(), ignored_names, ignored_groups)
    groups = set(
        tasks.exclude(group__isnull=True).exclude(group='').values_list('group', flat=True).distinct().order_by('group')
    )
    groups.update(row['group'] for row in pending_rows if row['group'])
    return sorted(groups)


@login_required
@require_GET
@user_passes_test(lambda u: u.is_superuser)
def task_report(request):
    """
    View of django-q2 tasks: what is still pending (scheduled or queued) and what has
    already run, with its success/failure status and result. Superuser only.
    """
    # tasks matching an admin-maintained IgnoredTask rule are dropped everywhere on this page
    ignored_names, ignored_groups = _ignore_rules()
    all_pending = [
        row
        for row in _scheduled_rows() + _queued_rows()
        if not _is_ignored(row['name'], row['group'], ignored_names, ignored_groups)
    ]

    # build the group dropdown from every group present, so it doesn't depend on the filters
    form = TaskFilterForm(request.GET or None, groups=_group_choices(all_pending, ignored_names, ignored_groups))

    name = ''
    group = ''
    if form.is_bound and form.is_valid():
        name = form.cleaned_data['name']
        group = form.cleaned_data['group']

    pending_rows = _filter_rows(all_pending, name, group)
    # queued rows have no scheduled time, so they sort ahead of the soonest scheduled run
    pending_rows.sort(key=lambda row: (row['when'] is not None, row['when']))

    tasks = _exclude_ignored(Task.objects.all(), ignored_names, ignored_groups)
    if name:
        tasks = tasks.filter(name__icontains=name)
    if group:
        tasks = tasks.filter(group=group)
    tasks = tasks.order_by('-stopped')

    paginator = Paginator(tasks, TASKS_PER_PAGE)
    try:
        page_obj = paginator.page(request.GET.get('page'))
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    filter_parameters = urlencode({k: v for k, v in (('name', name), ('group', group)) if v})

    # htmx polls this same view and swaps in just the tables, leaving the filter inputs alone
    template = '_task_tables.html' if _wants_partial(request) else 'task_report.html'

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
            'refresh_seconds': REFRESH_SECONDS,
        },
    )
