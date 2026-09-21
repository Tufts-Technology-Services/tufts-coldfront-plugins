import datetime
from unittest.mock import MagicMock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from django.utils import timezone
from django_q.models import OrmQ, Schedule, Task
from django_q.signing import SignedPackage
from django_q.tasks import schedule as create_schedule

from tufts_local.views import task_report
from tufts_local.views.task_report import _q_options


def make_user(is_superuser=False):
    user = MagicMock(name='user')
    user.is_authenticated = True
    user.is_superuser = is_superuser
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


def get_report(rf, query=''):
    request = rf.get(f'/task-report/{query}')
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

    def test_non_superuser_redirects_to_login(self, rf):
        request = rf.get('/task-report/')
        request.user = make_user(is_superuser=False)

        response = task_report(request)

        assert response.status_code == 302
        assert 'login' in response.url

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

    def test_result_is_escaped_in_the_detail_popover(self, rf):
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

    def test_group_filter_matches_exactly(self, rf):
        make_task('a' * 32, 'indexing', group='starfish')
        make_task('b' * 32, 'eligibility', group='ncq')

        response = get_report(rf, '?group=ncq')

        names = [t.name for t in response.context_data['page_obj']]
        assert names == ['eligibility']

    def test_group_choices_come_from_existing_tasks(self, rf):
        make_task('a' * 32, 'indexing', group='starfish')
        make_task('b' * 32, 'eligibility', group='ncq')

        response = get_report(rf)

        choices = response.context_data['form'].fields['group'].choices
        assert choices == [('', 'All'), ('ncq', 'ncq'), ('starfish', 'starfish')]

    def test_unknown_group_filter_is_ignored(self, rf):
        make_task('a' * 32, 'indexing', group='starfish')

        response = get_report(rf, '?group=nonexistent')

        assert response.context_data['form'].errors
        assert [t.name for t in response.context_data['page_obj']] == ['indexing']

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
        make_task('a' * 32, 'indexing', group='starfish')

        response = get_report(rf, '?name=index&group=starfish')

        assert response.context_data['filter_parameters'] == 'name=index&group=starfish&'


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

    def test_schedule_is_filtered_by_name_and_group(self, rf):
        self.make_pending_schedule()

        assert get_report(rf, '?name=alloc_activate_7').context_data['pending_rows']
        assert not get_report(rf, '?name=refresh_ncq').context_data['pending_rows']
        assert get_report(rf, '?group=starfish').context_data['pending_rows']

    def test_schedule_group_appears_in_dropdown(self, rf):
        self.make_pending_schedule(group='starfish')

        response = get_report(rf)

        assert ('starfish', 'starfish') in response.context_data['form'].fields['group'].choices

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
