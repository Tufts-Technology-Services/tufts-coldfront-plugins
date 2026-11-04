# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import base64
import datetime
import logging
from html.parser import HTMLParser
from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django_htmx.middleware import HtmxDetails
from django_q.models import OrmQ, Schedule, Task
from django_q.signing import SignedPackage
from django_q.tasks import schedule as create_schedule

from tufts_local.models import IgnoredTask, TaskAcknowledgement
from tufts_local.views import task_report
from tufts_local.views.task_report import _q_options


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


@pytest.fixture
def rf():
    return RequestFactory()


def get_report(rf, query='', htmx=False):
    # htmx sets the HX-Request header; django_htmx's middleware turns that into request.htmx
    headers = {'HTTP_HX_REQUEST': 'true'} if htmx else {}
    request = rf.get(f'/task-report/{query}', **headers)
    request.htmx = HtmxDetails(request)
    request.user = make_user(is_superuser=True)
    add_session(request)
    return task_report(request)


class TestTaskReportAccess:
    def test_anonymous_user_redirects_to_login(self, rf):
        request = rf.get('/task-report/')
        request.user = AnonymousUser()

        response = task_report(request)

        assert response.status_code == 302
        assert 'login' in response.url

    def test_ordinary_user_redirects_to_login(self, rf):
        request = rf.get('/task-report/')
        request.user = make_user()

        response = task_report(request)

        assert response.status_code == 302
        assert 'login' in response.url

    @pytest.mark.django_db
    @pytest.mark.urls('tufts_local.tests.urls')
    def test_staff_may_see_the_report(self, rf):
        """Staff acknowledge failures, so they need the page the Ack button lives on."""
        make_task('a' * 32, 'indexing')
        request = rf.get('/task-report/')
        request.htmx = HtmxDetails(request)
        request.user = make_user(is_staff=True)
        add_session(request)

        response = task_report(request)
        response.render()

        assert response.status_code == 200
        assert b'indexing' in response.content

    def test_post_not_allowed(self, rf):
        request = rf.post('/task-report/')
        request.user = make_user(is_superuser=True)

        response = task_report(request)

        assert response.status_code == 405


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestCompletedTasks:
    def test_successes_and_failures_are_both_listed(self, rf):
        make_task('a' * 32, 'add_sf_tags_alloc_activate_1', success=True)
        make_task('b' * 32, 'add_sf_tags_alloc_activate_2', success=False, result='TimeoutError: not indexed')

        response = get_report(rf)
        response.render()

        assert response.status_code == 200
        assert b'add_sf_tags_alloc_activate_1' in response.content
        assert b'add_sf_tags_alloc_activate_2' in response.content
        assert b'TimeoutError' in response.content

    def test_columns_are_in_order(self, rf):
        make_task('a' * 32, 'indexing')

        response = get_report(rf)
        response.render()

        # past the summary counts, which have a table of their own at the top of Completed
        completed = response.content.split(b'id="task-counts"', 1)[1].split(b'</table>', 1)[1]
        headings = [part.split(b'</th>', 1)[0] for part in completed.split(b'<th scope="col">')[1:]]
        assert headings == [
            b'Status',
            b'Function',
            b'Message',
            b'Started',
            b'<span class="visually-hidden">Duration</span>',
            b'Ack',
        ]

    def test_started_leaves_out_the_year(self, rf):
        task = make_task('a' * 32, 'indexing')

        response = get_report(rf)
        response.render()

        local = timezone.localtime(task.started)
        cell = f'title="{local:%Y-%m-%d %H:%M:%S}">{local:%m-%d %H:%M:%S}</td>'
        assert cell.encode() in response.content

    def test_message_comes_from_the_result(self, rf):
        make_task('a' * 32, 'dict_result', result={'message': 'quota updated', 'count': 3}, minutes_ago=1)
        make_task('b' * 32, 'string_result', result='TimeoutError: not indexed', minutes_ago=2)
        make_task('c' * 32, 'no_result', result=None, minutes_ago=3)

        response = get_report(rf)

        messages = [t.result_message for t in response.context_data['page_obj']]
        assert messages == ['quota updated', 'TimeoutError: not indexed', '']

    def test_auto_refresh_starts_off(self, rf):
        response = get_report(rf)
        response.render()

        checkbox = response.content.split(b'id="auto-refresh"', 1)[1].split(b'>', 1)[0]
        assert b'checked' not in checkbox

    def test_result_is_escaped_in_the_detail_modal(self, rf):
        # tracebacks carry quotes and angle brackets, which would otherwise break out of the attribute
        make_task('a' * 32, 'risky', result='<script>alert("x")</script>')

        response = get_report(rf)
        response.render()

        assert b'<script>alert' not in response.content
        assert b'&lt;script&gt;' in response.content

    def test_sorted_by_reverse_stopped(self, rf):
        make_task('a' * 32, 'oldest', minutes_ago=30)
        make_task('b' * 32, 'newest', minutes_ago=1)
        make_task('c' * 32, 'middle', minutes_ago=10)

        response = get_report(rf)

        names = [t.name for t in response.context_data['page_obj']]
        assert names == ['newest', 'middle', 'oldest']

    def test_name_filter_matches_substring(self, rf):
        make_task('a' * 32, 'add_sf_tags_alloc_activate_1')
        make_task('b' * 32, 'refresh_ncq_eligibility')

        response = get_report(rf, '?name=alloc_activate')

        names = [t.name for t in response.context_data['page_obj']]
        assert names == ['add_sf_tags_alloc_activate_1']

    @pytest.mark.parametrize(
        ('state', 'expected'),
        [
            ('', ['fine', 'open', 'signed_off']),
            ('success', ['fine']),
            ('failed', ['open', 'signed_off']),
            ('unacknowledged', ['open']),
            ('acknowledged', ['signed_off']),
            ('scheduled', []),
            ('queued', []),
        ],
    )
    def test_state_filter(self, rf, state, expected):
        make_task('a' * 32, 'fine', success=True, minutes_ago=1)
        make_task('b' * 32, 'open', success=False, minutes_ago=2)
        signed_off = make_task('c' * 32, 'signed_off', success=False, minutes_ago=3)
        TaskAcknowledgement.objects.create(task=signed_off)

        response = get_report(rf, f'?state={state}')

        assert [t.name for t in response.context_data['page_obj']] == expected

    def test_unknown_state_filter_is_ignored(self, rf):
        make_task('a' * 32, 'indexing')

        response = get_report(rf, '?state=nonexistent')

        assert response.context_data['form'].errors
        assert [t.name for t in response.context_data['page_obj']] == ['indexing']

    def test_group_is_no_longer_a_filter(self, rf):
        make_task('a' * 32, 'indexing', group='starfish')
        make_task('b' * 32, 'eligibility', group='ncq')

        response = get_report(rf, '?group=ncq')

        assert 'group' not in response.context_data['form'].fields
        assert len(response.context_data['page_obj'].object_list) == 2

    def test_pagination(self, rf):
        for i in range(55):
            make_task(f'{i:032d}', f'task_{i}', minutes_ago=i)

        first_page = get_report(rf)
        second_page = get_report(rf, '?page=2')

        assert first_page.context_data['is_paginated'] is True
        assert first_page.context_data['task_count'] == 55
        assert len(first_page.context_data['page_obj'].object_list) == 50
        assert len(second_page.context_data['page_obj'].object_list) == 5

    def test_filters_are_preserved_in_pagination_links(self, rf):
        make_task('a' * 32, 'indexing')

        response = get_report(rf, '?name=index&state=success')

        assert response.context_data['filter_parameters'] == 'name=index&state=success&'


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestPendingTasks:
    def make_pending_schedule(self, task_name='add_sf_tags_alloc_activate_7', group='starfish'):
        return create_schedule(
            'tufts_local.tasks.index_new_allocation',
            7,
            schedule_type=Schedule.ONCE,
            next_run=timezone.now() + datetime.timedelta(minutes=5),
            q_options={'task_name': task_name, 'group': group},
        )

    def test_schedule_name_and_group_are_parsed_from_kwargs(self, rf):
        self.make_pending_schedule()

        response = get_report(rf)

        rows = response.context_data['pending_rows']
        assert len(rows) == 1
        assert rows[0]['kind'] == 'Scheduled'
        assert rows[0]['name'] == 'add_sf_tags_alloc_activate_7'
        assert rows[0]['group'] == 'starfish'

    def test_schedule_is_filtered_by_name_and_state(self, rf):
        self.make_pending_schedule()

        assert get_report(rf, '?name=alloc_activate_7').context_data['pending_rows']
        assert not get_report(rf, '?name=refresh_ncq').context_data['pending_rows']
        assert get_report(rf, '?state=scheduled').context_data['pending_rows']
        assert not get_report(rf, '?state=queued').context_data['pending_rows']
        assert not get_report(rf, '?state=failed').context_data['pending_rows']

    def test_exhausted_schedules_are_excluded(self, rf):
        schedule = self.make_pending_schedule()
        Schedule.objects.filter(id=schedule.id).update(repeats=0)

        response = get_report(rf)

        assert response.context_data['pending_rows'] == []

    def test_last_run_status_is_attached(self, rf):
        task = make_task('a' * 32, 'add_sf_tags_alloc_activate_7', success=False)
        schedule = self.make_pending_schedule()
        Schedule.objects.filter(id=schedule.id).update(task=task.id)

        response = get_report(rf)

        assert response.context_data['pending_rows'][0]['last_run'].id == task.id

    def test_pending_renders(self, rf):
        self.make_pending_schedule()

        response = get_report(rf)
        response.render()

        assert response.status_code == 200
        assert b'add_sf_tags_alloc_activate_7' in response.content


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestQueuedTasks:
    """OrmQ rows only exist when Q_CLUSTER uses the ORM broker, so they're created directly here."""

    def make_queued_task(self, name='update_approvers', group='starfish'):
        payload = SignedPackage.dumps(
            {
                'id': 'd' * 32,
                'name': name,
                'group': group,
                'func': 'tufts_local.tasks.update_sf_approver_tags',
                'args': (1,),
                'kwargs': {},
            }
        )
        return OrmQ.objects.create(key='default', payload=payload)

    def test_queued_task_is_listed(self, rf):
        self.make_queued_task()

        response = get_report(rf)

        rows = response.context_data['pending_rows']
        assert len(rows) == 1
        assert rows[0]['kind'] == 'Queued'
        assert rows[0]['name'] == 'update_approvers'
        assert rows[0]['group'] == 'starfish'
        assert rows[0]['detail'] == 'Waiting'

    def test_queued_task_is_filtered(self, rf):
        self.make_queued_task()

        assert get_report(rf, '?name=approvers').context_data['pending_rows']
        assert not get_report(rf, '?name=index').context_data['pending_rows']
        assert get_report(rf, '?state=queued').context_data['pending_rows']
        assert not get_report(rf, '?state=scheduled').context_data['pending_rows']

    def test_unreadable_payload_does_not_break_the_page(self, rf):
        OrmQ.objects.create(key='default', payload='not a signed package')

        response = get_report(rf)
        response.render()

        assert response.status_code == 200
        assert response.context_data['pending_rows'][0]['func'] == 'unreadable payload'

    def test_queued_sorts_ahead_of_scheduled(self, rf):
        self.make_queued_task()
        create_schedule(
            'tufts_local.tasks.index_new_allocation',
            7,
            schedule_type=Schedule.ONCE,
            next_run=timezone.now() + datetime.timedelta(minutes=5),
            q_options={'task_name': 'add_sf_tags_alloc_activate_7', 'group': 'starfish'},
        )

        response = get_report(rf)

        assert [row['kind'] for row in response.context_data['pending_rows']] == ['Queued', 'Scheduled']


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestAutoRefresh:
    def test_full_page_polls_itself_with_htmx(self, rf):
        make_task('a' * 32, 'indexing')

        response = get_report(rf)
        response.render()

        assert b'id="task-tables"' in response.content
        assert b'indexing' in response.content
        assert b'hx-trigger="every 5s [shouldAutoRefresh()]"' in response.content
        assert b'hx-swap="innerHTML"' in response.content

    def test_htmx_request_returns_only_the_tables(self, rf):
        make_task('a' * 32, 'indexing')

        response = get_report(rf, htmx=True)
        response.render()

        assert response.status_code == 200
        assert response.template_name == 'tufts_local/_task_tables.html'
        assert b'indexing' in response.content
        # no page chrome: nothing to swap into, no filter form, no surrounding document
        assert b'id="task-tables"' not in response.content
        assert b'<form' not in response.content
        assert b'</html>' not in response.content

    def test_request_htmx_attribute_is_honoured(self, rf):
        # what django_htmx's HtmxMiddleware leaves on the request
        make_task('a' * 32, 'indexing')
        request = rf.get('/task-report/')
        request.user = make_user(is_superuser=True)
        request.htmx = True
        add_session(request)

        response = task_report(request)

        assert response.template_name == 'tufts_local/_task_tables.html'

    def test_plain_request_still_gets_the_full_page(self, rf):
        response = get_report(rf)

        assert response.template_name == 'tufts_local/task_report.html'

    def test_poll_keeps_the_active_filters(self, rf):
        make_task('a' * 32, 'indexing', success=True)
        make_task('b' * 32, 'eligibility', success=False)

        response = get_report(rf, '?state=failed', htmx=True)
        response.render()

        assert b'eligibility' in response.content
        assert b'indexing' not in response.content

    def test_poll_keeps_the_current_page(self, rf):
        for i in range(55):
            make_task(f'{i:032d}', f'task_{i}', minutes_ago=i)

        response = get_report(rf, '?page=2', htmx=True)

        assert response.context_data['page_obj'].number == 2

    def test_poll_url_carries_filters_and_page(self, rf):
        for i in range(55):
            make_task(f'{i:032d}', f'task_{i}', minutes_ago=i)

        response = get_report(rf, '?state=success&page=2')
        response.render()

        assert b'hx-get="/task-report/?state=success&amp;page=2"' in response.content

    def test_refresh_interval_is_published_to_the_template(self, rf):
        response = get_report(rf)
        response.render()

        assert response.context_data['refresh_seconds'] == 5
        assert b'every 5s' in response.content


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestIgnoredTasks:
    def ignore_name(self, pattern):
        return IgnoredTask.objects.create(field=IgnoredTask.NAME, pattern=pattern)

    def ignore_group(self, pattern):
        return IgnoredTask.objects.create(field=IgnoredTask.GROUP, pattern=pattern)

    def test_name_rule_matches_on_prefix(self, rf):
        make_task('a' * 32, 'add_sf_tags_alloc_activate_1')
        make_task('b' * 32, 'add_sf_tags_alloc_activate_2')
        make_task('c' * 32, 'refresh_ncq_eligibility')
        self.ignore_name('add_sf_tags')

        response = get_report(rf)

        assert [t.name for t in response.context_data['page_obj']] == ['refresh_ncq_eligibility']

    def test_name_rule_does_not_match_mid_string(self, rf):
        make_task('a' * 32, 'retry_add_sf_tags_alloc_activate_1')
        self.ignore_name('add_sf_tags')

        response = get_report(rf)

        assert [t.name for t in response.context_data['page_obj']] == ['retry_add_sf_tags_alloc_activate_1']

    def test_group_rule_matches_in_full(self, rf):
        make_task('a' * 32, 'indexing', group='starfish')
        make_task('b' * 32, 'eligibility', group='ncq')
        self.ignore_group('starfish')

        response = get_report(rf)

        assert [t.name for t in response.context_data['page_obj']] == ['eligibility']

    def test_group_rule_does_not_match_on_prefix(self, rf):
        make_task('a' * 32, 'indexing', group='starfish')
        self.ignore_group('star')

        response = get_report(rf)

        assert [t.name for t in response.context_data['page_obj']] == ['indexing']

    def test_matching_ignores_case(self, rf):
        make_task('a' * 32, 'Add_SF_Tags_1', group='Starfish')
        make_task('b' * 32, 'eligibility', group='ncq')
        self.ignore_name('add_sf')

        assert [t.name for t in get_report(rf).context_data['page_obj']] == ['eligibility']

        IgnoredTask.objects.all().delete()
        self.ignore_group('STARFISH')

        assert [t.name for t in get_report(rf).context_data['page_obj']] == ['eligibility']

    def test_tasks_without_a_group_survive_a_group_rule(self, rf):
        make_task('a' * 32, 'ungrouped', group=None)
        make_task('b' * 32, 'indexing', group='starfish')
        self.ignore_group('starfish')

        response = get_report(rf)

        assert [t.name for t in response.context_data['page_obj']] == ['ungrouped']

    def test_blank_pattern_does_not_hide_everything(self, rf):
        make_task('a' * 32, 'indexing')
        IgnoredTask.objects.create(field=IgnoredTask.NAME, pattern='   ')

        response = get_report(rf)

        assert [t.name for t in response.context_data['page_obj']] == ['indexing']

    def test_pending_rows_are_ignored_too(self, rf):
        create_schedule(
            'tufts_local.tasks.index_new_allocation',
            7,
            schedule_type=Schedule.ONCE,
            next_run=timezone.now() + datetime.timedelta(minutes=5),
            q_options={'task_name': 'add_sf_tags_alloc_activate_7', 'group': 'starfish'},
        )
        self.ignore_name('add_sf_tags')

        response = get_report(rf)

        assert response.context_data['pending_rows'] == []

    def test_ignored_tasks_are_hidden_silently(self, rf):
        make_task('a' * 32, 'add_sf_tags_alloc_activate_1')
        self.ignore_name('add_sf_tags')

        response = get_report(rf)
        response.render()

        assert b'add_sf_tags_alloc_activate_1' not in response.content
        # no notice, no count, no toggle: the rows are simply gone (aria-hidden is base.html's)
        assert b'ignor' not in response.content.lower()
        assert b'suppress' not in response.content.lower()

    def test_count_reflects_the_ignore_list(self, rf):
        make_task('a' * 32, 'add_sf_tags_alloc_activate_1')
        make_task('b' * 32, 'refresh_ncq_eligibility')
        self.ignore_name('add_sf_tags')

        response = get_report(rf)

        assert response.context_data['task_count'] == 1


class TestIgnoredTaskModel:
    def test_str_names_the_field_and_pattern(self):
        rule = IgnoredTask(field=IgnoredTask.GROUP, pattern='starfish')

        assert str(rule) == 'Group id (exact): starfish'

    def test_clean_strips_the_pattern(self):
        rule = IgnoredTask(field=IgnoredTask.NAME, pattern='  add_sf_tags  ')

        rule.clean()

        assert rule.pattern == 'add_sf_tags'


class TestQOptions:
    def test_returns_empty_for_unparseable_kwargs(self):
        assert _q_options(Schedule(kwargs='not a dict')) == {}

    def test_returns_empty_when_no_kwargs(self):
        assert _q_options(Schedule(kwargs=None)) == {}

    def test_returns_empty_when_kwargs_has_no_q_options(self):
        assert _q_options(Schedule(kwargs="{'retries': 3}")) == {}

    def test_parses_q_options(self):
        schedule = Schedule(kwargs="{'q_options': {'task_name': 'x', 'group': 'starfish'}}")

        assert _q_options(schedule) == {'task_name': 'x', 'group': 'starfish'}

    def test_keyword_syntax_kwargs_are_not_a_parse_failure(self, caplog):
        """django_q also accepts bare keyword arguments in Schedule.kwargs; a schedule
        written that way has no q_options, but it isn't malformed and logs nothing."""
        schedule = Schedule(id=14, kwargs='timeout=600')

        with caplog.at_level(logging.DEBUG, logger='tufts_local.views.task_report'):
            assert _q_options(schedule) == {}

        assert caplog.records == []

    def test_parses_q_options_from_keyword_syntax(self):
        schedule = Schedule(kwargs="q_options={'task_name': 'x', 'group': 'starfish'}, timeout=600")

        assert _q_options(schedule) == {'task_name': 'x', 'group': 'starfish'}

    def test_malformed_kwargs_log_at_debug_not_warning(self, caplog):
        """Unparseable kwargs are someone else's schedule to fix, not an error in this
        report, so they must not surface at warning level on every refresh."""
        with caplog.at_level(logging.DEBUG, logger='tufts_local.views.task_report'):
            assert _q_options(Schedule(id=14, kwargs='timeout=')) == {}

        assert 'Could not parse kwargs for schedule 14' in caplog.text
        assert [record.levelno for record in caplog.records] == [logging.DEBUG]


def _nested_in_task_tables(content, element_id):
    """Is the element with this id a descendant of #task-tables in the rendered page?

    Counts div depth from the opening of #task-tables, which is enough here: the region is
    a div and so is everything that nests inside it.
    """

    class Finder(HTMLParser):
        def __init__(self):
            super().__init__()
            self.depth = None
            self.found = False

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if self.depth is None:
                if attributes.get('id') == 'task-tables':
                    self.depth = 1
                return
            if attributes.get('id') == element_id:
                self.found = True
            if tag == 'div':
                self.depth += 1

        def handle_endtag(self, tag):
            if self.depth is not None and tag == 'div':
                self.depth -= 1
                if self.depth == 0:
                    self.depth = None

    finder = Finder()
    finder.feed(content.decode())
    assert finder.depth is None, 'never found the end of #task-tables; the check would be vacuous'
    return finder.found


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestDetailModal:
    """The tables here are swapped in by htmx, and nothing instantiates a popover on
    swapped-in content: coldfront creates them once on DOMContentLoaded, and a page script
    can't make its own, since the bundle exposes no Bootstrap API. Bootstrap's modal click
    handler is delegated on document, so a modal keeps working on rows that arrive later.
    These tests hold the report to that, because the symptom of getting it wrong is a
    control that renders perfectly and does nothing when clicked."""

    def test_the_page_carries_one_modal_for_every_row(self, rf):
        make_task('a' * 32, 'one')
        make_task('b' * 32, 'two')

        response = get_report(rf)
        response.render()

        assert response.content.count(b'id="task-detail-modal"') == 1
        assert response.content.count(b'data-bs-target="#task-detail-modal"') == 2

    def test_the_modal_offers_a_copy_button(self, rf):
        make_task('a' * 32, 'boom')

        response = get_report(rf)
        response.render()

        assert b'id="task-detail-copy"' in response.content
        assert b'function taskDetailText()' in response.content
        assert not _nested_in_task_tables(response.content, 'task-detail-copy')

    def test_the_modal_sits_outside_the_swapped_region(self, rf):
        """hx-swap="innerHTML" replaces everything inside #task-tables, so a modal nested
        in there is gone the moment the first poll lands, and every trigger on the page
        then opens nothing at all -- silently, and only after a few seconds have passed.

        Nesting has to be checked structurally: the modal is absent from the swapped
        partial and present in the page whether it is nested or not, so neither one on its
        own can tell the two arrangements apart.
        """
        make_task('a' * 32, 'boom')

        response = get_report(rf)
        response.render()

        assert b'id="task-detail-modal"' in response.content
        assert not _nested_in_task_tables(response.content, 'task-detail-modal')

    def test_the_trigger_carries_the_whole_detail(self, rf):
        task = make_task('a' * 32, 'risky', group='starfish', result='TimeoutError: not indexed')

        response = get_report(rf)
        response.render()

        assert b'data-detail-title="risky"' in response.content
        assert f'data-detail-template="task-detail-{task.id}"'.encode() in response.content
        detail = response.content.split(f'<template id="task-detail-{task.id}">'.encode(), 1)[1]
        detail = detail.split(b'</template>', 1)[0]
        for heading in (b'Name', b'ID', b'Function', b'Group', b'Started', b'Stopped', b'Attempts'):
            assert b'>' + heading + b'</dt>' in detail
        assert b'Args' in detail and b'Kwargs' in detail and b'Result' in detail
        assert b'starfish' in detail
        assert b'TimeoutError: not indexed' in detail

    def test_the_detail_says_whether_a_failure_was_acknowledged(self, rf):
        task = make_task('a' * 32, 'boom', success=False)
        user = get_user_model().objects.create(username='someone')
        TaskAcknowledgement.objects.create(task=task, acknowledged_by=user, reason='vendor outage')

        response = get_report(rf)
        response.render()

        detail = response.content.split(b'<template id="task-detail-', 1)[1].split(b'</template>', 1)[0]
        assert b'by someone' in detail
        assert b'vendor outage' in detail

    def test_the_detail_survives_a_swap(self, rf):
        """The partial htmx swaps in has to carry its own trigger, or the detail is
        reachable on the first page load and never again."""
        make_task('a' * 32, 'boom')

        response = get_report(rf, htmx=True)
        response.render()

        assert response.template_name == 'tufts_local/_task_tables.html'
        assert b'data-bs-target="#task-detail-modal"' in response.content


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestAckColumn:
    def test_a_failure_offers_the_button(self, rf):
        make_task('a' * 32, 'boom', success=False)

        response = get_report(rf)
        response.render()

        assert b'>Ack</button>' in response.content
        assert b'hx-post="/task-acknowledge/' + b'a' * 32 + b'/"' in response.content

    def test_an_acknowledged_failure_shows_who_and_when(self, rf):
        task = make_task('a' * 32, 'boom', success=False)
        user = get_user_model().objects.create(username='someone')
        TaskAcknowledgement.objects.create(task=task, acknowledged_by=user)

        response = get_report(rf)
        response.render()

        assert b'Acknowledged' in response.content
        assert b'Acknowledged by someone' in response.content
        assert b'>Undo</button>' in response.content
        assert b'>Ack</button>' not in response.content

    def test_a_success_offers_nothing(self, rf):
        """Acknowledging is for failures; there is nothing to sign off on a task that worked."""
        make_task('a' * 32, 'fine', success=True)

        response = get_report(rf)
        response.render()

        assert b'>Ack</button>' not in response.content
        # the badge, not the word: the summary counts have an Acknowledged column
        assert b'</i> Acknowledged' not in response.content

    def test_the_button_carries_csrf_for_htmx(self, rf):
        """Every hx-post in the table is rejected as a CSRF failure without this header.

        coldfront 1.1.9 supplies it on <body> in common/base.html, so the page no longer
        sets it itself -- which is worth a test rather than an assumption, since losing it
        would break the Ack button and nothing else.
        """
        make_task('a' * 32, 'boom', success=False)

        response = get_report(rf)
        response.render()

        assert b'hx-headers=' in response.content
        assert b'x-csrftoken' in response.content.lower()

    def test_the_button_remembers_the_filter_and_page(self, rf):
        make_task('a' * 32, 'boom', success=False)

        response = get_report(rf, '?state=failed')
        response.render()

        assert response.context_data['return_to'] == 'state=failed&page=1'
        assert b'value="state=failed&amp;page=1"' in response.content

    def test_the_acknowledgement_comes_back_with_the_row(self, rf):
        """Every row asks whether it has been acknowledged. Without select_related that is
        one extra query per row -- 50 a page, every five seconds."""
        make_task('a' * 32, 'boom', success=False)

        response = get_report(rf)

        task = response.context_data['page_obj'].object_list[0]
        assert 'acknowledgement' in task._state.fields_cache


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestAcknowledgeAllShown:
    def rendered(self, rf, query=''):
        response = get_report(rf, query)
        response.render()
        return response

    def test_offered_with_the_count_of_unacknowledged_failures(self, rf):
        make_task('a' * 32, 'boom', success=False)
        make_task('b' * 32, 'boom', success=False)
        acknowledged = make_task('c' * 32, 'boom', success=False)
        TaskAcknowledgement.objects.create(task=acknowledged)
        make_task('d' * 32, 'fine')

        content = self.rendered(rf).content.decode()

        assert 'Acknowledge all shown (2 unacknowledged failures)' in content
        assert 'action="/task-acknowledge-bulk/?before=' in content

    def test_follows_the_filters(self, rf):
        make_task('a' * 32, 'index_new_allocation', success=False)
        make_task('b' * 32, 'refresh_ncq_eligibility', success=False)

        response = self.rendered(rf, '?name=index')

        assert response.context_data['unacknowledged_count'] == 1
        assert 'name=index' in response.context_data['acknowledge_all_url']

    @pytest.mark.parametrize(('state', 'count'), [('failed', 1), ('unacknowledged', 1), ('success', 0), ('queued', 0)])
    def test_follows_the_state(self, rf, state, count):
        """A state that hides the unacknowledged failures hides the button that would acknowledge them."""
        make_task('a' * 32, 'boom', success=False)

        response = self.rendered(rf, f'?state={state}')

        assert response.context_data['unacknowledged_count'] == count

    def test_not_offered_when_nothing_needs_it(self, rf):
        make_task('a' * 32, 'fine')

        assert 'Acknowledge all shown' not in self.rendered(rf).content.decode()

    def test_the_poll_skips_counting(self, rf):
        """The form is outside the polled tables, so an htmx refresh has no use for the count."""
        make_task('a' * 32, 'boom', success=False)

        response = get_report(rf, htmx=True)

        assert response.context_data['unacknowledged_count'] == 0


def period_counts(response, label_start):
    """One period of the summary counts, as ({status: count}, total), picked by how its label starts."""
    period = next(p for p in response.context_data['periods'] if p['label'].startswith(label_start))
    return {count['status']: count['count'] for count in period['counts']}, period['total']


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestSummaryCounts:
    """The counts by period at the top of Completed, which replaced the task summary page."""

    @pytest.fixture(autouse=True)
    def two_hour_recent_window(self, settings):
        settings.TASK_RECENT_WINDOW_HOURS = 2

    def test_tasks_land_in_the_period_matching_their_age(self, rf):
        make_task('a' * 32, 'just_now', minutes_ago=1)
        make_task('b' * 32, 'this_afternoon', minutes_ago=5 * 60)
        make_task('c' * 32, 'yesterday', minutes_ago=30 * 60)

        response = get_report(rf)

        assert period_counts(response, 'Last 2 hours')[1] == 1
        assert period_counts(response, 'Earlier')[1] == 2

    def test_counts_are_broken_down_by_status(self, rf):
        make_task('a' * 32, 'ok_1', success=True, minutes_ago=5 * 60)
        make_task('b' * 32, 'ok_2', success=True, minutes_ago=6 * 60)
        make_task('c' * 32, 'boom', success=False, minutes_ago=7 * 60)
        acknowledged = make_task('d' * 32, 'old_boom', success=False, minutes_ago=48 * 60)
        TaskAcknowledgement.objects.create(task=acknowledged)

        response = get_report(rf)

        # an acknowledged failure is still counted, under its own heading: signed off, not erased
        assert period_counts(response, 'Earlier') == ({'Succeeded': 2, 'Failed': 1, 'Acknowledged': 1}, 4)

    def test_every_status_is_reported_even_at_zero(self, rf):
        """'Failed 0' is the answer someone scanning the counts wants; a missing column isn't."""
        response = get_report(rf)

        assert period_counts(response, 'Earlier') == ({'Succeeded': 0, 'Failed': 0, 'Acknowledged': 0}, 0)

    def test_the_periods_partition_every_task(self, rf):
        """Split at one clock reading, no task is counted twice or falls between the periods;
        minutes either side of the boundary is close enough to catch a second reading."""
        for index, minutes_ago in enumerate((1, 119, 121, 23 * 60, 25 * 60)):
            make_task(f'{index:032d}', f'task_{index}', minutes_ago=minutes_ago)

        response = get_report(rf)

        recent = period_counts(response, 'Last 2 hours')[1]
        earlier = period_counts(response, 'Earlier')[1]
        assert (recent, earlier) == (2, 3)
        assert recent + earlier == Task.objects.count()

    def test_the_counts_follow_the_filters(self, rf):
        """They describe the tasks listed below them, so the two can't disagree."""
        make_task('a' * 32, 'index_new_allocation', success=False, minutes_ago=1)
        make_task('b' * 32, 'refresh_ncq_eligibility', success=True, minutes_ago=1)

        by_name = get_report(rf, '?name=index')
        by_state = get_report(rf, '?state=success')

        assert period_counts(by_name, 'Last 2 hours') == ({'Succeeded': 0, 'Failed': 1, 'Acknowledged': 0}, 1)
        assert period_counts(by_state, 'Last 2 hours') == ({'Succeeded': 1, 'Failed': 0, 'Acknowledged': 0}, 1)

    def test_ignored_tasks_are_left_out(self, rf):
        make_task('a' * 32, 'add_sf_tags_alloc_activate_1', minutes_ago=1)
        make_task('b' * 32, 'refresh_ncq_eligibility', minutes_ago=1)
        IgnoredTask.objects.create(field=IgnoredTask.NAME, pattern='add_sf_tags')

        response = get_report(rf)

        assert period_counts(response, 'Last 2 hours')[1] == 1

    def test_the_labels_follow_the_recent_window_setting(self, rf, settings):
        settings.TASK_RECENT_WINDOW_HOURS = 1

        response = get_report(rf)
        response.render()

        assert [p['label'] for p in response.context_data['periods']] == ['Last 1 hour', 'Earlier']
        assert b'Last 1 hour' in response.content

    def test_the_default_recent_window(self, rf, settings):
        del settings.TASK_RECENT_WINDOW_HOURS
        make_task('a' * 32, 'recent', minutes_ago=23 * 60)
        make_task('b' * 32, 'old', minutes_ago=25 * 60)

        response = get_report(rf)

        assert period_counts(response, 'Last 24 hours')[1] == 1
        assert period_counts(response, 'Earlier')[1] == 1

    def test_the_counts_table_is_rendered(self, rf):
        make_task('a' * 32, 'boom', success=False, minutes_ago=1)

        response = get_report(rf)
        response.render()

        counts = response.content.decode().split('id="task-counts"', 1)[1].split('</table>', 1)[0]
        assert [h.split('<', 1)[0] for h in counts.split('class="text-end">')[1:5]] == [
            'Succeeded',
            'Failed',
            'Acknowledged',
            'Total',
        ]
        assert 'text-danger fw-semibold">1</td>' in counts

    def test_the_poll_brings_fresh_counts(self, rf):
        make_task('a' * 32, 'boom', success=False, minutes_ago=1)

        response = get_report(rf, htmx=True)
        response.render()

        assert response.template_name == 'tufts_local/_task_tables.html'
        assert b'id="task-counts"' in response.content


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestFoldingSections:
    """Pending and the summary counts fold away, and the page remembers how the reader left them."""

    def rendered(self, rf, htmx=False):
        response = get_report(rf, htmx=htmx)
        response.render()
        return response.content.decode()

    def test_pending_starts_open(self, rf):
        content = self.rendered(rf)

        assert '<div id="pending-tasks" class="collapse show" data-remember-collapse>' in content
        assert 'href="#pending-tasks"\n     role="button" aria-expanded="true"' in content

    def test_the_summary_starts_closed(self, rf):
        content = self.rendered(rf)

        assert '<div id="task-counts" class="collapse" data-remember-collapse>' in content
        assert 'class="section-toggle collapsed' in content
        assert 'href="#task-counts"\n   role="button" aria-expanded="false"' in content

    def test_pending_shows_its_count_in_the_heading(self, rf):
        create_schedule(
            'tufts_local.tasks.index_new_allocation',
            schedule_type=Schedule.ONCE,
            next_run=timezone.now() + datetime.timedelta(minutes=5),
        )

        content = self.rendered(rf)

        heading = content.split('href="#pending-tasks"', 1)[1].split('</h3>', 1)[0]
        assert '<span class="badge bg-secondary">1</span>' in heading

    def test_the_page_remembers_and_restores_them(self, rf):
        """Every poll re-renders the sections in their default state, so the page has to put
        the reader's choice back after each swap, or they'd snap shut every few seconds."""
        content = self.rendered(rf)

        assert "document.addEventListener('shown.bs.collapse', rememberSection);" in content
        assert "document.addEventListener('hidden.bs.collapse', rememberSection);" in content
        assert 'restoreSections(event.target);' in content

    def test_the_sections_come_back_with_every_poll(self, rf):
        content = self.rendered(rf, htmx=True)

        assert content.count('data-remember-collapse') == 2


def store_raw_pickle(task_id, column, pickled_bytes):
    """Write a pickled column directly, the way an older deployment's code could have left it."""
    raw = base64.b64encode(pickled_bytes).decode()
    with connection.cursor() as cursor:
        cursor.execute(f'UPDATE django_q_task SET {column} = %s WHERE id = %s', [raw, task_id])


# a pickle naming a class in a module that doesn't exist: loading it raises ModuleNotFoundError
UNLOADABLE_PICKLE = b'cgone_module\nGone\n.'


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestUnreadablePickles:
    """
    A result, args or kwargs holding an instance of a class this process can no longer import.

    picklefield swallows that failure and hands back the stored base64 text instead, which the
    report would then show as though it were the task's result. The report reads the columns
    itself so it can say what happened.
    """

    def test_an_unloadable_result_says_so(self, rf, caplog):
        task = make_task('a' * 32, 'gone')
        store_raw_pickle(task.id, 'result', UNLOADABLE_PICKLE)

        with caplog.at_level(logging.WARNING, logger='tufts_local.views.task_report'):
            response = get_report(rf)
            response.render()

        shown = response.context_data['page_obj'].object_list[0]
        note = "<unreadable: ModuleNotFoundError: No module named 'gone_module'>"
        assert shown.result_message == note
        content = response.content.decode()
        # escaped, in the Message column and again in the detail modal's Result
        assert content.count('&lt;unreadable: ModuleNotFoundError') == 2
        assert base64.b64encode(UNLOADABLE_PICKLE).decode() not in content
        assert f'Could not unpickle result of task {task.id}' in caplog.text

    def test_unloadable_args_and_kwargs_say_so_too(self, rf):
        task = make_task('a' * 32, 'gone')
        store_raw_pickle(task.id, 'args', UNLOADABLE_PICKLE)
        store_raw_pickle(task.id, 'kwargs', UNLOADABLE_PICKLE)

        response = get_report(rf)
        response.render()

        detail = response.content.decode().split(f'<template id="task-detail-{task.id}">', 1)[1]
        assert detail.count('&lt;unreadable: ModuleNotFoundError') == 2
        # the result itself was fine, so it is still shown as it was
        assert '>done</pre>' in detail

    def test_one_bad_task_leaves_the_rest_alone(self, rf):
        make_task('a' * 32, 'fine', result={'message': 'all good'}, minutes_ago=1)
        bad = make_task('b' * 32, 'gone', minutes_ago=2)
        store_raw_pickle(bad.id, 'result', UNLOADABLE_PICKLE)

        response = get_report(rf)

        messages = [t.result_message for t in response.context_data['page_obj']]
        assert messages[0] == 'all good'
        assert messages[1].startswith('<unreadable: ModuleNotFoundError')

    def test_readable_values_still_decode(self, rf):
        Task.objects.create(
            id='a' * 32,
            name='typed',
            func='tufts_local.tasks.index_new_allocation',
            args=(4821, 'pi_lab01'),
            kwargs={'force': True},
            result={'message': 'ok', 'count': 3},
            started=timezone.now(),
            stopped=timezone.now(),
            success=True,
        )

        task = get_report(rf).context_data['page_obj'].object_list[0]

        assert task.args == (4821, 'pi_lab01')
        assert task.kwargs == {'force': True}
        assert task.result == {'message': 'ok', 'count': 3}

    def test_empty_values_stay_empty(self, rf):
        make_task('a' * 32, 'nothing', result=None)

        task = get_report(rf).context_data['page_obj'].object_list[0]

        assert task.result is None
        assert task.result_message == ''

    def test_the_pickled_columns_are_fetched_once(self, rf):
        """Each comes back as text for _unpickled, and is deferred so the page query doesn't
        also select it for the field to unpickle: results can be large, and there are 50 a page."""
        make_task('a' * 32, 'one')

        with CaptureQueriesContext(connection) as queries:
            get_report(rf)

        (page_query,) = [q['sql'] for q in queries if 'FROM "django_q_task"' in q['sql'] and 'LIMIT' in q['sql']]
        for column in ('args', 'kwargs', 'result'):
            assert page_query.count(f'"django_q_task"."{column}"') == 1, column
