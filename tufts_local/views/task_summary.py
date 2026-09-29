# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import datetime
import logging

from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Count, Q
from django.http import JsonResponse
from django.template.response import TemplateResponse
from django.utils import timezone
from django.views.decorators.http import require_GET
from django_q.models import Schedule, Task

from tufts_local.views.task_report import _exclude_ignored, _ignore_rules, _is_ignored, _may_view, _q_options

logger = logging.getLogger(__name__)

# the segment boundaries, measured back from the moment the page is built
RECENT_WINDOW = datetime.timedelta(hours=2)
AGGREGATE_WINDOW = datetime.timedelta(hours=24)

# longer cell values are cut short in the table and offered in full in a popover
MAX_CELL_CHARS = 60

# how often the indicator refreshes itself; it is a corner badge, not a live console
INDICATOR_REFRESH_SECONDS = 30

# The three outcomes a finished task can be in, as (aggregate name, label). django-q
# records only a boolean, so 'Acknowledged' is this app's own third state: a failure
# someone has looked at and signed off (see models.TaskAcknowledgement). Counting it
# apart from 'Failed' keeps 'Failed' meaning what the corner badge means -- failures
# nobody has dealt with yet -- so the page explains the badge instead of contradicting it.
STATUS_LABELS = (('succeeded', 'Success'), ('failed', 'Failed'), ('acknowledged', 'Acknowledged'))


def _columns(model):
    """
    Every stored field of a model, as (attribute name, column heading).

    Read off the model rather than listed by hand so that the detail tables keep showing
    the whole record even if django-q adds a column.
    """
    return [(field.name, field.verbose_name) for field in model._meta.concrete_fields]


def _text(value):
    if value is None:
        return ''
    if isinstance(value, datetime.datetime):
        return timezone.localtime(value).strftime('%Y-%m-%d %H:%M:%S')
    return str(value)


def _value(instance, name):
    """
    One field's value, as something safe to render.

    Reading a field can fail: args, kwargs and result are pickles, and one referring to a
    class this process can't import raises on attribute access. A single such task
    shouldn't empty the page, so the value says so and the row survives.
    """
    try:
        # a field with choices stores a code ('O'); show what that code means ('Once')
        display = getattr(instance, f'get_{name}_display', None)
        return display() if display else getattr(instance, name)
    except Exception as e:
        logger.warning(f'Could not read {name} of {instance.__class__.__name__} {instance.pk}: {e}')
        return f'<unreadable: {e.__class__.__name__}>'


def _cell(instance, name):
    """One table cell: the field's text, plus the untruncated text when it won't fit."""
    value = _value(instance, name)
    text = _text(value)
    return {
        'name': name,
        'text': text,
        'full': text if len(text) > MAX_CELL_CHARS else '',
        'flag': value if isinstance(value, bool) else None,
    }


def _detail_rows(instances, columns):
    return [[_cell(instance, name) for name, _heading in columns] for instance in instances]


def _records(instances, columns):
    """
    The same whole-record rows as dicts, for the JSON endpoint.

    Values are stringified exactly as the page renders them: args, kwargs and result are
    pickled Python objects, so there is no faithful JSON of them to hand back anyway.
    """
    return [{name: _text(_value(instance, name)) for name, _heading in columns} for instance in instances]


def _status_counts(tasks):
    """
    How many tasks in a segment ended each way, counted in the database.

    Every status is always present, including at zero: 'Failed 0' is the answer someone
    scanning this page is looking for, and a missing row doesn't give it to them.
    """
    counts = tasks.aggregate(
        succeeded=Count('id', filter=Q(success=True)),
        failed=Count('id', filter=Q(success=False, acknowledgement__isnull=True)),
        acknowledged=Count('id', filter=Q(success=False, acknowledgement__isnull=False)),
    )
    return [{'status': label, 'count': counts[name]} for name, label in STATUS_LABELS]


def _segment(key, label, tasks, since=None, until=None):
    counts = _status_counts(tasks)
    return {
        'key': key,
        'label': label,
        'since': since,
        'until': until,
        'counts': counts,
        'total': sum(count['count'] for count in counts),
    }


def _pending_schedules(ignored_names, ignored_groups):
    """
    The schedules that will still fire, minus the ones on the ignore list.

    A schedule's name and group live inside the repr in its kwargs column rather than in
    columns of their own, so the ignore list has to be applied in Python. repeats=0 means
    the schedule is spent and the scheduler skips it, so this leaves it out as well.
    """
    kept = []
    for schedule in Schedule.objects.exclude(repeats=0):
        options = _q_options(schedule)
        name = schedule.name or options.get('task_name') or ''
        if not _is_ignored(name, options.get('group') or '', ignored_names, ignored_groups):
            kept.append(schedule)
    return kept


def _summary():
    """
    The whole summary, shared by the page, the JSON endpoint and the indicator.

    Three windows back from one clock reading, so they partition the completed tasks
    exactly: no task lands in two of them and none lands in none of them. The oldest two
    are counted; the last two hours is counted *and* listed, since that is the part still
    worth acting on. Scheduled tasks have not finished at all, so they are kept out of
    every window and returned on their own.
    """
    ignored_names, ignored_groups = _ignore_rules()
    tasks = _exclude_ignored(Task.objects.all(), ignored_names, ignored_groups)

    now = timezone.now()
    recent_start = now - RECENT_WINDOW
    day_start = now - AGGREGATE_WINDOW

    return {
        'generated_at': now,
        'older': _segment(
            'older',
            'Finished more than 24 hours ago',
            tasks.filter(stopped__lt=day_start),
            until=day_start,
        ),
        'earlier': _segment(
            'earlier',
            'Finished 2 to 24 hours ago',
            tasks.filter(stopped__gte=day_start, stopped__lt=recent_start),
            since=day_start,
            until=recent_start,
        ),
        'recent': _segment(
            'recent',
            'Finished in the last 2 hours',
            tasks.filter(stopped__gte=recent_start),
            since=recent_start,
        ),
        'recent_tasks': list(tasks.filter(stopped__gte=recent_start).order_by('-stopped')),
        'schedules': _pending_schedules(ignored_names, ignored_groups),
    }


def _counts_by_status(segment):
    return {count['status']: count['count'] for count in segment['counts']}


def _failed(segment):
    """Failures nobody has acknowledged yet -- the ones still asking for attention."""
    return _counts_by_status(segment)['Failed']


def _wants_json(request):
    return request.GET.get('format') == 'json'


def _isoformat(value):
    return value.isoformat() if value else None


def _segment_json(segment):
    return {
        'label': segment['label'],
        'since': _isoformat(segment['since']),
        'until': _isoformat(segment['until']),
        'counts': _counts_by_status(segment),
        'total': segment['total'],
    }


@login_required
@require_GET
@user_passes_test(_may_view)
def task_summary(request):
    """
    Completed django-q tasks counted by outcome, plus the recent ones in full.

    Add ?format=json for the same content as JSON. In both, a period's 'Failed' count is
    the failures still unacknowledged; the ones signed off on the task report are counted
    under 'Acknowledged' instead.
    """
    summary = _summary()
    recent_columns = _columns(Task)
    schedule_columns = _columns(Schedule)

    if _wants_json(request):
        return JsonResponse(
            {
                'generated_at': _isoformat(summary['generated_at']),
                'segments': [_segment_json(summary[key]) for key in ('older', 'earlier', 'recent')],
                'recent_tasks': _records(summary['recent_tasks'], recent_columns),
                'schedules': _records(summary['schedules'], schedule_columns),
            }
        )

    return TemplateResponse(
        request,
        'tufts_local/task_summary.html',
        {
            # the last two hours is listed row by row below, so it isn't in the counts table
            'segments': [summary['older'], summary['earlier']],
            'recent_columns': recent_columns,
            'recent_rows': _detail_rows(summary['recent_tasks'], recent_columns),
            'recent_count': summary['recent']['total'],
            'recent_since': summary['recent']['since'],
            'schedule_columns': schedule_columns,
            'schedule_rows': _detail_rows(summary['schedules'], schedule_columns),
            'schedule_count': len(summary['schedules']),
            'generated_at': summary['generated_at'],
            # the templates truncate to the same width the popover threshold uses, so a
            # cell never offers "see the rest" when there is no rest to see
            'max_cell_chars': MAX_CELL_CHARS,
            'indicator_refresh_seconds': INDICATOR_REFRESH_SECONDS,
        },
    )


@login_required
@require_GET
@user_passes_test(_may_view)
def task_summary_indicator(request):
    """
    The summary reduced to counts, for the status indicator in the page corner.

    Returns an HTML fragment to swap into that corner, or the same numbers as JSON with
    ?format=json. Wire it up with htmx from whichever template owns the corner:

        <span id="task-indicator"
              hx-get="{% url 'task-summary-indicator' %}"
              hx-trigger="load, every 30s"
              hx-swap="innerHTML"></span>

    Counts only, which is all a corner badge has room for; the detail is a click away on
    the summary page itself. Acknowledged failures don't count towards failed_recent or
    failed_before, so signing one off on the task report puts the badge back to green.
    """
    summary = _summary()
    recent, earlier, older = summary['recent'], summary['earlier'], summary['older']
    failed_recent = _failed(recent)
    # failures older than the red badge's window still need answering, so they colour the
    # badge amber rather than waiting to be found in the tooltip
    failed_before = _failed(earlier) + _failed(older)

    if _wants_json(request):
        return JsonResponse(
            {
                'generated_at': _isoformat(summary['generated_at']),
                'failed_recent': failed_recent,
                'failed_before': failed_before,
                'segments': {key: _segment_json(summary[key]) for key in ('older', 'earlier', 'recent')},
                'scheduled': len(summary['schedules']),
            }
        )

    return TemplateResponse(
        request,
        'tufts_local/_task_summary_indicator.html',
        {
            'failed_recent': failed_recent,
            'failed_before': failed_before,
            'succeeded_recent': _counts_by_status(recent)['Success'],
            'failed_earlier': _failed(earlier),
            'failed_older': _failed(older),
            'scheduled': len(summary['schedules']),
            'generated_at': summary['generated_at'],
        },
    )
