import ast
import logging
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.template.response import TemplateResponse
from django.views.decorators.http import require_GET
from django_q.models import OrmQ, Schedule, Task

from tufts_local.forms import TaskFilterForm

logger = logging.getLogger(__name__)

TASKS_PER_PAGE = 50


def _q_options(schedule):
    """
    Return the q_options dict a schedule was created with.

    django_q's Schedule model has no name/group columns of its own: anything passed as
    q_options to schedule() is stored as the repr of {'q_options': {...}} in the kwargs
    TextField, and parsed back out with ast.literal_eval when the scheduler runs it.
    """
    if not schedule.kwargs:
        return {}
    try:
        kwargs = ast.literal_eval(schedule.kwargs)
    except (ValueError, SyntaxError):
        logger.warning(f'Could not parse kwargs for schedule {schedule.id}: {schedule.kwargs}')
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


def _group_choices(pending_rows):
    groups = set(
        Task.objects.exclude(group__isnull=True)
        .exclude(group='')
        .values_list('group', flat=True)
        .distinct()
        .order_by('group')
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
    # build the group dropdown from every group present, so it doesn't depend on the filters
    all_pending = _scheduled_rows() + _queued_rows()
    form = TaskFilterForm(request.GET or None, groups=_group_choices(all_pending))

    name = ''
    group = ''
    if form.is_bound and form.is_valid():
        name = form.cleaned_data['name']
        group = form.cleaned_data['group']

    pending_rows = _filter_rows(all_pending, name, group)
    # queued rows have no scheduled time, so they sort ahead of the soonest scheduled run
    pending_rows.sort(key=lambda row: (row['when'] is not None, row['when']))

    tasks = Task.objects.all()
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

    return TemplateResponse(
        request,
        'tufts_local/task_report.html',
        {
            'form': form,
            'pending_rows': pending_rows,
            'page_obj': page_obj,
            'is_paginated': paginator.num_pages > 1,
            'task_count': paginator.count,
            'filter_parameters': f'{filter_parameters}&' if filter_parameters else '',
        },
    )
