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
from tufts_local.views import task_summary, task_summary_indicator
from tufts_local.views.task_summary import _cell


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


@pytest.fixture
def rf():
    return RequestFactory()


def get_summary(rf, query=''):
    request = rf.get(f'/task-summary/{query}')
    request.user = make_user(is_superuser=True)
    add_session(request)
    return task_summary(request)


def get_indicator(rf, query='', user=None):
    request = rf.get(f'/task-summary-indicator/{query}')
    request.user = user or make_user(is_superuser=True)
    add_session(request)
    return task_summary_indicator(request)


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

        _older, older_total = segment(response, 'more than 24 hours')
        _recent_ish, recent_ish_total = segment(response, '2 to 24 hours')
        assert older_total == 1
        assert recent_ish_total == 1
        assert response.context_data['recent_count'] == 1
        # the count and the listing are separate queries, so they are checked to agree
        assert len(response.context_data['recent_rows']) == 1

    def test_counts_are_broken_down_by_status(self, rf):
        make_task('a' * 32, 'ok_1', success=True, minutes_ago=5 * 60)
        make_task('b' * 32, 'ok_2', success=True, minutes_ago=6 * 60)
        make_task('c' * 32, 'boom', success=False, minutes_ago=7 * 60)
        make_task('d' * 32, 'old_boom', success=False, minutes_ago=48 * 60)

        response = get_summary(rf)

        assert segment(response, '2 to 24 hours') == ({'Success': 2, 'Failed': 1, 'Acknowledged': 0}, 3)
        assert segment(response, 'more than 24 hours') == ({'Success': 0, 'Failed': 1, 'Acknowledged': 0}, 1)

    def test_both_statuses_are_reported_even_at_zero(self, rf):
        """'Failed 0' is the answer someone scanning this page wants; a missing row isn't."""
        response = get_summary(rf)

        for fragment in ('more than 24 hours', '2 to 24 hours'):
            counts, total = segment(response, fragment)
            assert counts == {'Success': 0, 'Failed': 0, 'Acknowledged': 0}
            assert total == 0

    def test_the_windows_partition_every_task(self, rf):
        """No task may be counted twice or fall between two windows, so all the boundaries
        are taken from one clock reading; minutes either side of each is close enough to
        catch a second reading being used."""
        for index, minutes_ago in enumerate((1, 119, 121, 23 * 60, 25 * 60)):
            make_task(f'{index:032d}', f'task_{index}', minutes_ago=minutes_ago)

        response = get_summary(rf)

        _older, older_total = segment(response, 'more than 24 hours')
        _mid, mid_total = segment(response, '2 to 24 hours')
        assert older_total == 1
        assert mid_total == 2
        assert response.context_data['recent_count'] == 2
        assert older_total + mid_total + response.context_data['recent_count'] == Task.objects.count()


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

        assert segment(response, 'more than 24 hours') == ({'Success': 0, 'Failed': 0, 'Acknowledged': 0}, 0)
        assert segment(response, '2 to 24 hours') == ({'Success': 0, 'Failed': 0, 'Acknowledged': 0}, 0)
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

        assert segment(response, '2 to 24 hours') == ({'Success': 1, 'Failed': 0, 'Acknowledged': 0}, 1)

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

        assert b'data-toggle="popover"' in response.content
        # truncated in the cell, complete in the popover
        assert b'x' * 500 in response.content
        assert b'x' * 61 not in response.content.split(b'data-content=')[0]

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

        assert [segment['total'] for segment in body['segments']] == [1, 1, 1]
        assert body['segments'][1]['counts'] == {'Success': 0, 'Failed': 1, 'Acknowledged': 0}

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
class TestIndicator:
    def test_staff_may_see_it(self, rf):
        response = get_indicator(rf, user=make_user(is_staff=True))

        assert response.status_code == 200

    def test_ordinary_users_may_not(self, rf):
        response = get_indicator(rf, user=make_user())

        assert response.status_code == 302
        assert 'login' in response.url

    def test_anonymous_users_may_not(self, rf):
        request = rf.get('/task-summary-indicator/')
        request.user = AnonymousUser()

        response = task_summary_indicator(request)

        assert response.status_code == 302

    def test_post_not_allowed(self, rf):
        request = rf.post('/task-summary-indicator/')
        request.user = make_user(is_superuser=True)

        assert task_summary_indicator(request).status_code == 405

    def test_json_counts(self, rf):
        make_task('a' * 32, 'boom', success=False, minutes_ago=1)
        make_task('b' * 32, 'fine', minutes_ago=1)
        make_task('c' * 32, 'old_boom', success=False, minutes_ago=30 * 60)
        make_schedule()

        body = payload(get_indicator(rf, '?format=json'))

        assert body['failed_recent'] == 1
        assert body['segments']['recent']['counts'] == {'Success': 1, 'Failed': 1, 'Acknowledged': 0}
        assert body['segments']['older']['counts'] == {'Success': 0, 'Failed': 1, 'Acknowledged': 0}
        assert body['scheduled'] == 1

    def test_json_is_counts_only(self, rf):
        """The indicator rides along on every page load once it is in the corner, so it
        stays counts: no task name, no arguments, no results. The detail is one click
        away on the summary page, which the same people can open."""
        make_task('a' * 32, 'secret_task_name', result='secret result')
        make_schedule(task_name='secret_schedule_name')

        body = payload(get_indicator(rf, '?format=json'))

        assert 'secret' not in json.dumps(body)

    def test_fragment_flags_recent_failures(self, rf):
        make_task('a' * 32, 'boom', success=False, minutes_ago=1)

        response = get_indicator(rf)
        response.render()

        assert response.template_name == 'tufts_local/_task_summary_indicator.html'
        assert b'badge-danger' in response.content
        assert b'1 failed' in response.content

    def test_fragment_is_green_when_nothing_failed(self, rf):
        make_task('a' * 32, 'fine', minutes_ago=1)

        response = get_indicator(rf)
        response.render()

        assert b'badge-success' in response.content
        assert b'badge-danger' not in response.content
        assert b'badge-warning' not in response.content

    @pytest.mark.parametrize('minutes_ago', (5 * 60, 30 * 60), ids=('earlier_today', 'older'))
    def test_fragment_is_amber_for_older_failures(self, rf, minutes_ago):
        """A failure outside the red window is still a failure; leaving the badge green
        until someone hovers would let an overnight one go unnoticed."""
        make_task('a' * 32, 'boom', success=False, minutes_ago=minutes_ago)

        response = get_indicator(rf)
        response.render()

        assert b'badge-warning' in response.content
        assert b'1 failed earlier' in response.content
        assert b'badge-danger' not in response.content

    def test_recent_failures_outrank_older_ones(self, rf):
        make_task('a' * 32, 'boom', success=False, minutes_ago=1)
        make_task('b' * 32, 'old_boom', success=False, minutes_ago=30 * 60)

        response = get_indicator(rf)
        response.render()

        assert b'badge-danger' in response.content
        assert b'badge-warning' not in response.content

    def test_older_failures_are_added_up_across_both_windows(self, rf):
        make_task('a' * 32, 'boom', success=False, minutes_ago=5 * 60)
        make_task('b' * 32, 'old_boom', success=False, minutes_ago=30 * 60)

        response = get_indicator(rf)
        response.render()

        assert b'2 failed earlier' in response.content
        assert payload(get_indicator(rf, '?format=json'))['failed_before'] == 2

    def test_fragment_names_no_tasks(self, rf):
        make_task('a' * 32, 'secret_task_name', result='secret result')

        response = get_indicator(rf)
        response.render()

        assert b'secret' not in response.content

    def test_fragment_is_a_fragment(self, rf):
        response = get_indicator(rf)
        response.render()

        # nothing for the corner to unwrap: no document, and no element to swap into
        assert b'</html>' not in response.content
        assert b'hx-get' not in response.content

    def test_the_badge_links_to_the_summary(self, rf):
        """Both endpoints answer to the same audience, so the link always leads somewhere
        its reader can actually open."""
        for user in (make_user(is_superuser=True), make_user(is_staff=True)):
            response = get_indicator(rf, user=user)
            response.render()

            assert b'href="/task-summary/"' in response.content


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestIndicatorWiring:
    def test_the_page_polls_the_indicator(self, rf):
        response = get_summary(rf)
        response.render()

        assert b'hx-get="/task-summary-indicator/"' in response.content
        assert b'hx-trigger="load, every 30s"' in response.content


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
    """Acknowledging is the only thing that clears the indicator, so this is the part of
    that feature worth pinning down from the summary's side."""

    def acknowledge(self, task):
        return TaskAcknowledgement.objects.create(task=task)

    def test_an_acknowledged_failure_moves_out_of_failed(self, rf):
        task = make_task('a' * 32, 'boom', success=False, minutes_ago=5 * 60)
        self.acknowledge(task)

        response = get_summary(rf)

        # still counted, still part of the total: signed off, not erased
        assert segment(response, '2 to 24 hours') == ({'Success': 0, 'Failed': 0, 'Acknowledged': 1}, 1)

    def test_a_red_badge_goes_green(self, rf):
        task = make_task('a' * 32, 'boom', success=False, minutes_ago=1)
        make_task('b' * 32, 'fine', minutes_ago=1)

        assert b'badge-danger' in self.rendered(rf)

        self.acknowledge(task)

        assert b'badge-success' in self.rendered(rf)

    def test_an_amber_badge_goes_green(self, rf):
        task = make_task('a' * 32, 'old_boom', success=False, minutes_ago=30 * 60)

        assert b'badge-warning' in self.rendered(rf)

        self.acknowledge(task)

        assert b'badge-warning' not in self.rendered(rf)

    def test_the_json_counts_drop_it_too(self, rf):
        recent = make_task('a' * 32, 'boom', success=False, minutes_ago=1)
        older = make_task('b' * 32, 'old_boom', success=False, minutes_ago=30 * 60)

        before = payload(get_indicator(rf, '?format=json'))
        self.acknowledge(recent)
        self.acknowledge(older)
        after = payload(get_indicator(rf, '?format=json'))

        assert (before['failed_recent'], before['failed_before']) == (1, 1)
        assert (after['failed_recent'], after['failed_before']) == (0, 0)

    def test_the_task_is_still_listed_in_full(self, rf):
        """The row stays on the page -- acknowledging is not hiding, which is what the
        IgnoredTask list is for."""
        task = make_task('a' * 32, 'boom', success=False, minutes_ago=1)
        self.acknowledge(task)

        response = get_summary(rf)
        response.render()

        assert b'boom' in response.content

    @staticmethod
    def rendered(rf):
        response = get_indicator(rf)
        response.render()
        return response.content
