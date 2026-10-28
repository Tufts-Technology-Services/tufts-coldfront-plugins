# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import datetime
import json
from unittest.mock import MagicMock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from django.utils import timezone
from django_q.models import Schedule, Task
from django_q.tasks import schedule as create_schedule

from tufts_local.models import IgnoredTask, TaskAcknowledgement
from tufts_local.views import task_summary
from tufts_local.views.task_summary import STATUS_LABELS, _cell


def make_user(is_superuser=False, is_staff=False):
    user = MagicMock(name='user')
    user.is_authenticated = True
    user.is_superuser = is_superuser
    # spelled out because a MagicMock would otherwise answer every permission check truthily
    user.is_staff = is_staff
    user.username = 'rdms_admin'
    return user


def add_session(request):
    # rendering the template requires django_su's context processor, which reads request.session
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()


def make_task(task_id, name, group='starfish', success=True, minutes_ago=0, result='done'):
    stopped = timezone.now() - datetime.timedelta(minutes=minutes_ago)
    return Task.objects.create(
        id=task_id,
        name=name,
        func='tufts_local.tasks.index_new_allocation',
        group=group,
        started=stopped - datetime.timedelta(seconds=30),
        stopped=stopped,
        success=success,
        result=result,
    )


def make_schedule(task_name='add_sf_tags_alloc_activate_7', group='starfish'):
    return create_schedule(
        'tufts_local.tasks.index_new_allocation',
        7,
        schedule_type=Schedule.ONCE,
        next_run=timezone.now() + datetime.timedelta(minutes=5),
        q_options={'task_name': task_name, 'group': group},
    )


@pytest.fixture(autouse=True)
def two_hour_recent_window(settings):
    """These tests place tasks either side of a 2-hour boundary; the default is wider (test_default_recent_window)."""
    settings.TASK_RECENT_WINDOW_HOURS = 2


@pytest.fixture
def rf():
    return RequestFactory()


def get_summary(rf, query=''):
    request = rf.get(f'/task-summary/{query}')
    request.user = make_user(is_superuser=True)
    add_session(request)
    return task_summary(request)


def payload(response):
    return json.loads(response.content)


def segment(response, fragment):
    """The one segment whose label contains fragment, as {status: count} plus its total."""
    match = next(s for s in response.context_data['segments'] if fragment in s['label'])
    return {count['status']: count['count'] for count in match['counts']}, match['total']


def column_names(columns):
    return [name for name, _heading in columns]


def cell_named(row, columns, name):
    return row[column_names(columns).index(name)]


class TestTaskSummaryAccess:
    def test_anonymous_user_redirects_to_login(self, rf):
        request = rf.get('/task-summary/')
        request.user = AnonymousUser()

        response = task_summary(request)

        assert response.status_code == 302
        assert 'login' in response.url

    def test_ordinary_user_redirects_to_login(self, rf):
        request = rf.get('/task-summary/')
        request.user = make_user()

        response = task_summary(request)

        assert response.status_code == 302
        assert 'login' in response.url

    @pytest.mark.django_db
    @pytest.mark.urls('tufts_local.tests.urls')
    def test_staff_may_see_the_whole_thing(self, rf):
        """Staff get the detail rows too, not just the counts the indicator carries."""
        make_task('a' * 32, 'just_now')
        request = rf.get('/task-summary/')
        request.user = make_user(is_staff=True)
        add_session(request)

        response = task_summary(request)
        response.render()

        assert response.status_code == 200
        assert b'just_now' in response.content

    def test_post_not_allowed(self, rf):
        request = rf.post('/task-summary/')
        request.user = make_user(is_superuser=True)

        response = task_summary(request)

        assert response.status_code == 405


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestSegments:
    def test_tasks_land_in_the_segment_matching_their_age(self, rf):
        make_task('a' * 32, 'just_now', minutes_ago=1)
        make_task('b' * 32, 'this_afternoon', minutes_ago=5 * 60)
        make_task('c' * 32, 'yesterday', minutes_ago=30 * 60)

        response = get_summary(rf)

        _older, older_total = segment(response, 'more than 2 hours')
        assert older_total == 2
        assert response.context_data['recent_count'] == 1
        # the count and the listing are separate queries, so they are checked to agree
        assert len(response.context_data['recent_rows']) == 1

    def test_counts_are_broken_down_by_status(self, rf):
        make_task('a' * 32, 'ok_1', success=True, minutes_ago=5 * 60)
        make_task('b' * 32, 'ok_2', success=True, minutes_ago=6 * 60)
        make_task('c' * 32, 'boom', success=False, minutes_ago=7 * 60)
        make_task('d' * 32, 'old_boom', success=False, minutes_ago=48 * 60)

        response = get_summary(rf)

        assert segment(response, 'more than 2 hours') == ({'Success': 2, 'Failed': 2, 'Acknowledged': 0}, 4)

    def test_both_statuses_are_reported_even_at_zero(self, rf):
        """'Failed 0' is the answer someone scanning this page wants; a missing row isn't."""
        response = get_summary(rf)

        assert segment(response, 'more than 2 hours') == ({'Success': 0, 'Failed': 0, 'Acknowledged': 0}, 0)

    def test_the_windows_partition_every_task(self, rf):
        """No task may be counted twice or fall between the two windows, so the boundary is
        taken from one clock reading; minutes either side of it is close enough to catch a
        second reading being used."""
        for index, minutes_ago in enumerate((1, 119, 121, 23 * 60, 25 * 60)):
            make_task(f'{index:032d}', f'task_{index}', minutes_ago=minutes_ago)

        response = get_summary(rf)

        _older, older_total = segment(response, 'more than 2 hours')
        assert older_total == 3
        assert response.context_data['recent_count'] == 2
        assert older_total + response.context_data['recent_count'] == Task.objects.count()


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestRecentTasks:
    def test_recent_tasks_are_rows_not_counts(self, rf):
        for index in range(3):
            make_task(f'{index:032d}', f'task_{index}', minutes_ago=index)

        response = get_summary(rf)

        assert len(response.context_data['recent_rows']) == 3

    def test_rows_carry_every_field_of_the_model(self, rf):
        make_task('a' * 32, 'just_now')

        response = get_summary(rf)

        columns = response.context_data['recent_columns']
        assert column_names(columns) == [field.name for field in Task._meta.concrete_fields]
        assert len(response.context_data['recent_rows'][0]) == len(columns)

    def test_rows_are_newest_first(self, rf):
        make_task('a' * 32, 'oldest', minutes_ago=90)
        make_task('b' * 32, 'newest', minutes_ago=1)
        make_task('c' * 32, 'middle', minutes_ago=45)

        response = get_summary(rf)

        columns = response.context_data['recent_columns']
        names = [cell_named(row, columns, 'name')['text'] for row in response.context_data['recent_rows']]
        assert names == ['newest', 'middle', 'oldest']

    def test_failure_is_flagged_for_the_status_badge(self, rf):
        make_task('a' * 32, 'boom', success=False)

        response = get_summary(rf)

        row = response.context_data['recent_rows'][0]
        assert cell_named(row, response.context_data['recent_columns'], 'success')['flag'] is False

    def test_long_values_keep_their_full_text(self, rf):
        make_task('a' * 32, 'chatty', result='x' * 500)

        response = get_summary(rf)

        result = cell_named(response.context_data['recent_rows'][0], response.context_data['recent_columns'], 'result')
        assert result['full'] == 'x' * 500

    def test_short_values_have_nothing_to_expand(self, rf):
        make_task('a' * 32, 'terse', result='done')

        response = get_summary(rf)

        result = cell_named(response.context_data['recent_rows'][0], response.context_data['recent_columns'], 'result')
        assert result['full'] == ''


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestScheduledTasks:
    def test_schedules_are_rows_with_every_field(self, rf):
        make_schedule()

        response = get_summary(rf)

        columns = response.context_data['schedule_columns']
        assert column_names(columns) == [field.name for field in Schedule._meta.concrete_fields]
        assert response.context_data['schedule_count'] == 1
        assert len(response.context_data['schedule_rows'][0]) == len(columns)

    def test_schedules_stay_out_of_the_completed_segments(self, rf):
        make_schedule()

        response = get_summary(rf)

        assert segment(response, 'more than 2 hours') == ({'Success': 0, 'Failed': 0, 'Acknowledged': 0}, 0)
        assert response.context_data['recent_rows'] == []

    def test_schedule_type_is_shown_as_a_word(self, rf):
        make_schedule()

        response = get_summary(rf)

        row = response.context_data['schedule_rows'][0]
        assert cell_named(row, response.context_data['schedule_columns'], 'schedule_type')['text'] == 'Once'

    def test_exhausted_schedules_are_excluded(self, rf):
        schedule = make_schedule()
        Schedule.objects.filter(id=schedule.id).update(repeats=0)

        response = get_summary(rf)

        assert response.context_data['schedule_rows'] == []


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestIgnoredTasks:
    """The same admin-maintained ignore list the task report honours applies here."""

    def test_ignored_tasks_are_left_out_of_the_counts(self, rf):
        make_task('a' * 32, 'add_sf_tags_alloc_activate_1', minutes_ago=5 * 60)
        make_task('b' * 32, 'refresh_ncq_eligibility', minutes_ago=5 * 60)
        IgnoredTask.objects.create(field=IgnoredTask.NAME, pattern='add_sf_tags')

        response = get_summary(rf)

        assert segment(response, 'more than 2 hours') == ({'Success': 1, 'Failed': 0, 'Acknowledged': 0}, 1)

    def test_ignored_tasks_are_left_out_of_the_recent_rows(self, rf):
        make_task('a' * 32, 'indexing', group='starfish')
        make_task('b' * 32, 'eligibility', group='ncq')
        IgnoredTask.objects.create(field=IgnoredTask.GROUP, pattern='starfish')

        response = get_summary(rf)

        columns = response.context_data['recent_columns']
        names = [cell_named(row, columns, 'name')['text'] for row in response.context_data['recent_rows']]
        assert names == ['eligibility']

    def test_ignored_schedules_are_left_out(self, rf):
        make_schedule(task_name='add_sf_tags_alloc_activate_7')
        IgnoredTask.objects.create(field=IgnoredTask.NAME, pattern='add_sf_tags')

        response = get_summary(rf)

        assert response.context_data['schedule_rows'] == []


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestRendering:
    def test_page_renders_counts_rows_and_schedules(self, rf):
        make_task('a' * 32, 'just_now', minutes_ago=1)
        make_task('b' * 32, 'earlier', minutes_ago=5 * 60)
        make_schedule()

        response = get_summary(rf)
        response.render()

        assert response.status_code == 200
        assert b'just_now' in response.content
        assert b'add_sf_tags_alloc_activate_7' in response.content
        # the older task is a count only, so its name is nowhere on the page
        assert b'earlier' not in response.content

    def test_every_status_row_is_labelled_with_the_status_it_counts(self, rf):
        """The labels were hard-coded Success-or-else-Failed, written when there were two
        statuses. Once Acknowledged was added to the view, its row rendered a red "Failed"
        badge beside a number that was not the failure count -- two "Failed" rows per period.
        The other tests here all read context_data, which was right the whole time."""
        make_task('a' * 32, 'boom', success=False, minutes_ago=5 * 60)

        response = get_summary(rf)
        response.render()

        periods = len(response.context_data['segments'])
        for _name, label in STATUS_LABELS:
            assert response.content.count(f'>{label}</span>'.encode()) == periods

    def test_result_is_escaped(self, rf):
        make_task('a' * 32, 'risky', result='<script>alert("x")</script>')

        response = get_summary(rf)
        response.render()

        assert b'<script>alert' not in response.content
        assert b'&lt;script&gt;' in response.content

    def test_long_result_is_offered_in_a_popover(self, rf):
        make_task('a' * 32, 'chatty', result='x' * 500)

        response = get_summary(rf)
        response.render()

        assert b'data-bs-toggle="popover"' in response.content
        # truncated in the cell, complete in the popover. A popover is still the right
        # control here: this table is rendered with the page, so coldfront's initPopovers()
        # reaches it -- unlike the task report's, which htmx swaps and which uses a modal.
        assert b'<span class="small">' + b'x' * 59 + '…'.encode() in response.content
        assert b"<pre class='small mb-0'>" + b'x' * 500 in response.content

    def test_empty_states(self, rf):
        response = get_summary(rf)
        response.render()

        assert b'No tasks have finished since' in response.content
        assert b'There are no scheduled tasks to display.' in response.content


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestJsonEndpoint:
    def test_returns_json(self, rf):
        response = get_summary(rf, '?format=json')

        assert response.status_code == 200
        assert response['Content-Type'] == 'application/json'

    def test_segments_carry_their_counts(self, rf):
        make_task('a' * 32, 'just_now', minutes_ago=1)
        make_task('b' * 32, 'earlier', success=False, minutes_ago=5 * 60)
        make_task('c' * 32, 'yesterday', minutes_ago=30 * 60)

        body = payload(get_summary(rf, '?format=json'))

        assert [segment['total'] for segment in body['segments']] == [2, 1]
        assert body['segments'][0]['counts'] == {'Success': 1, 'Failed': 1, 'Acknowledged': 0}

    def test_recent_tasks_carry_every_field(self, rf):
        make_task('a' * 32, 'just_now')

        body = payload(get_summary(rf, '?format=json'))

        assert len(body['recent_tasks']) == 1
        assert set(body['recent_tasks'][0]) == {field.name for field in Task._meta.concrete_fields}
        assert body['recent_tasks'][0]['name'] == 'just_now'

    def test_schedules_carry_every_field(self, rf):
        make_schedule()

        body = payload(get_summary(rf, '?format=json'))

        assert len(body['schedules']) == 1
        assert set(body['schedules'][0]) == {field.name for field in Schedule._meta.concrete_fields}

    def test_older_tasks_are_counts_only(self, rf):
        make_task('a' * 32, 'yesterday', minutes_ago=30 * 60)

        body = payload(get_summary(rf, '?format=json'))

        assert body['segments'][0]['total'] == 1
        assert body['recent_tasks'] == []
        assert 'yesterday' not in json.dumps(body)

    def test_timestamps_are_iso_8601(self, rf):
        body = payload(get_summary(rf, '?format=json'))

        datetime.datetime.fromisoformat(body['generated_at'])
        datetime.datetime.fromisoformat(body['segments'][0]['until'])
        assert body['segments'][0]['since'] is None

    def test_staff_may_read_the_json(self, rf):
        request = rf.get('/task-summary/?format=json')
        request.user = make_user(is_staff=True)

        response = task_summary(request)

        assert response.status_code == 200

    def test_ordinary_users_may_not(self, rf):
        request = rf.get('/task-summary/?format=json')
        request.user = make_user()

        response = task_summary(request)

        assert response.status_code == 302


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestRecentWindow:
    def test_default_recent_window(self, rf, settings):
        del settings.TASK_RECENT_WINDOW_HOURS
        make_task('a' * 32, 'boom', minutes_ago=23 * 60)
        make_task('b' * 32, 'old', minutes_ago=25 * 60)

        response = get_summary(rf)
        response.render()

        assert response.context_data['recent_count'] == 1
        assert b'Finished in the last 24 hours' in response.content
        assert segment(response, 'more than 24 hours')[1] == 1

    def test_the_labels_follow_the_setting(self, rf, settings):
        settings.TASK_RECENT_WINDOW_HOURS = 1

        response = get_summary(rf)
        response.render()

        assert b'Finished in the last 1 hour <' in response.content
        assert b'more than 1 hour ago is a count only' in response.content
        assert segment(response, 'more than 1 hour')[1] == 0


class TestCell:
    def test_unreadable_field_does_not_break_the_row(self):
        """A pickled result referring to a class this process can't import raises on
        access; the report has to keep rendering around it."""

        class Exploding:
            pk = 'a' * 32

            @property
            def result(self):
                raise ModuleNotFoundError('no module named gone')

        cell = _cell(Exploding(), 'result')

        assert cell['text'] == '<unreadable: ModuleNotFoundError>'


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestAcknowledgedFailures:
    """Acknowledging is what clears a failure's alert (see test_task_failures.py); this is the
    summary's side of it."""

    def acknowledge(self, task):
        return TaskAcknowledgement.objects.create(task=task)

    def test_an_acknowledged_failure_moves_out_of_failed(self, rf):
        task = make_task('a' * 32, 'boom', success=False, minutes_ago=5 * 60)
        self.acknowledge(task)

        response = get_summary(rf)

        # still counted, still part of the total: signed off, not erased
        assert segment(response, 'more than 2 hours') == ({'Success': 0, 'Failed': 0, 'Acknowledged': 1}, 1)

    def test_the_task_is_still_listed_in_full(self, rf):
        """The row stays on the page -- acknowledging is not hiding, which is what the
        IgnoredTask list is for."""
        task = make_task('a' * 32, 'boom', success=False, minutes_ago=1)
        self.acknowledge(task)

        response = get_summary(rf)
        response.render()

        assert b'boom' in response.content
