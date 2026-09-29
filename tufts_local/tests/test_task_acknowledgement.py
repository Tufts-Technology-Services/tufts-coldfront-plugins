# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import datetime
from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import IntegrityError, transaction
from django.http import Http404
from django.test import RequestFactory
from django.utils import timezone
from django_q.models import Task

from tufts_local.models import TaskAcknowledgement
from tufts_local.views import acknowledge_task, unacknowledge_task

TASK_ID = 'a' * 32


def make_mock_user(is_superuser=False, is_staff=False):
    user = MagicMock(name='user')
    user.is_authenticated = True
    user.is_superuser = is_superuser
    # spelled out because a MagicMock would otherwise answer every permission check truthily
    user.is_staff = is_staff
    user.username = 'rdms_admin'
    return user


def make_real_user(username='rdms_admin', is_superuser=True, is_staff=False):
    """A database user, needed wherever an acknowledgement actually records who made it."""
    return get_user_model().objects.create(username=username, is_superuser=is_superuser, is_staff=is_staff)


def make_task(task_id=TASK_ID, name='add_sf_tags_alloc_activate_1', success=False, minutes_ago=1):
    stopped = timezone.now() - datetime.timedelta(minutes=minutes_ago)
    return Task.objects.create(
        id=task_id,
        name=name,
        func='tufts_local.tasks.index_new_allocation',
        group='starfish',
        started=stopped - datetime.timedelta(seconds=30),
        stopped=stopped,
        success=success,
        result='TimeoutError: not indexed',
    )


@pytest.fixture
def rf():
    return RequestFactory()


def post(rf, view, task_id=TASK_ID, user=None, htmx=False, **data):
    headers = {'HTTP_HX_REQUEST': 'true'} if htmx else {}
    request = rf.post(f'/task-acknowledge/{task_id}/', data, **headers)
    request.user = user if user is not None else make_mock_user(is_superuser=True)
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    MessageMiddleware(lambda r: None).process_request(request)
    return view(request, task_id)


class TestAccess:
    def test_anonymous_user_redirects_to_login(self, rf):
        request = rf.post(f'/task-acknowledge/{TASK_ID}/')
        request.user = AnonymousUser()

        response = acknowledge_task(request, TASK_ID)

        assert response.status_code == 302
        assert 'login' in response.url

    def test_ordinary_user_redirects_to_login(self, rf):
        request = rf.post(f'/task-acknowledge/{TASK_ID}/')
        request.user = make_mock_user()

        response = acknowledge_task(request, TASK_ID)

        assert response.status_code == 302
        assert 'login' in response.url

    @pytest.mark.parametrize('view', (acknowledge_task, unacknowledge_task))
    def test_get_not_allowed(self, rf, view):
        request = rf.get(f'/task-acknowledge/{TASK_ID}/')
        request.user = make_mock_user(is_superuser=True)

        assert view(request, TASK_ID).status_code == 405


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestAcknowledging:
    def test_records_who_and_when(self, rf):
        make_task()
        user = make_real_user()
        before = timezone.now()

        post(rf, acknowledge_task, user=user)

        acknowledgement = TaskAcknowledgement.objects.get()
        assert acknowledgement.task_id == TASK_ID
        assert acknowledgement.acknowledged_by == user
        assert acknowledgement.acknowledged_at >= before

    def test_staff_may_acknowledge(self, rf):
        make_task()
        staff = make_real_user(is_superuser=False, is_staff=True)

        post(rf, acknowledge_task, user=staff)

        assert TaskAcknowledgement.objects.get().acknowledged_by == staff

    def test_acknowledging_twice_is_harmless(self, rf):
        """A double-click, a replayed POST and two people on the same row all have to land
        on one acknowledgement rather than an integrity error."""
        make_task()
        first = make_real_user(username='first')
        second = make_real_user(username='second')

        post(rf, acknowledge_task, user=first)
        response = post(rf, acknowledge_task, user=second)

        assert response.status_code in (200, 302)
        acknowledgement = TaskAcknowledgement.objects.get()
        assert acknowledgement.acknowledged_by == first

    def test_successful_tasks_cannot_be_acknowledged(self, rf):
        """Nothing to acknowledge -- and a signed-off success would read as a failure
        someone had dealt with in every query that goes looking for one."""
        make_task(success=True)

        post(rf, acknowledge_task, user=make_real_user())

        assert not TaskAcknowledgement.objects.exists()

    def test_unknown_task_is_a_404(self, rf):
        with pytest.raises(Http404):
            post(rf, acknowledge_task, task_id='f' * 32, user=make_real_user())


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestUndoing:
    def test_removes_the_acknowledgement(self, rf):
        task = make_task()
        TaskAcknowledgement.objects.create(task=task, acknowledged_by=make_real_user())

        post(rf, unacknowledge_task, user=make_real_user(username='someone_else'))

        assert not TaskAcknowledgement.objects.exists()

    def test_undoing_nothing_is_harmless(self, rf):
        make_task()

        response = post(rf, unacknowledge_task, user=make_real_user())

        assert response.status_code in (200, 302)
        assert not TaskAcknowledgement.objects.exists()


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestResponses:
    def test_htmx_gets_the_cell_alone(self, rf):
        make_task()

        response = post(rf, acknowledge_task, user=make_real_user(), htmx=True)
        response.render()

        assert response.template_name == 'tufts_local/_task_ack_cell.html'
        assert b'Acknowledged' in response.content
        assert b'Undo' in response.content
        # a cell, not a page: nothing to unwrap and no table around it
        assert b'</html>' not in response.content
        assert b'<table' not in response.content

    def test_undo_swaps_the_button_back(self, rf):
        task = make_task()
        TaskAcknowledgement.objects.create(task=task, acknowledged_by=make_real_user())

        response = post(rf, unacknowledge_task, user=make_real_user(username='someone_else'), htmx=True)
        response.render()

        assert b'>Ack</button>' in response.content
        assert b'Acknowledged' not in response.content

    def test_a_plain_post_returns_to_the_report(self, rf):
        """Without htmx the buttons are still real forms, so the view has to send the
        reader back to the filter and page they were looking at."""
        make_task()

        response = post(rf, acknowledge_task, user=make_real_user(), return_to='group=starfish&page=2')

        assert response.status_code == 302
        assert response.url == '/task-report/?group=starfish&page=2'

    def test_a_plain_post_without_a_return_target(self, rf):
        make_task()

        response = post(rf, acknowledge_task, user=make_real_user())

        assert response.url == '/task-report/'

    def test_the_swapped_cell_keeps_the_return_target(self, rf):
        """The cell htmx swaps in holds the next click's forms, so it has to carry the
        filter and page forward or the fallback stops working after one use."""
        make_task()

        response = post(rf, acknowledge_task, user=make_real_user(), htmx=True, return_to='group=starfish&page=2')
        response.render()

        assert b'value="group=starfish&amp;page=2"' in response.content


@pytest.mark.django_db
class TestModel:
    def test_str_names_the_task_and_the_person(self):
        task = make_task()
        acknowledgement = TaskAcknowledgement.objects.create(task=task, acknowledged_by=make_real_user())

        assert str(acknowledgement) == f'{TASK_ID} acknowledged by rdms_admin'

    def test_one_acknowledgement_per_task(self):
        task = make_task()
        TaskAcknowledgement.objects.create(task=task)

        with pytest.raises(IntegrityError), transaction.atomic():
            TaskAcknowledgement.objects.create(task=task)

    def test_deleting_the_task_deletes_the_acknowledgement(self):
        task = make_task()
        TaskAcknowledgement.objects.create(task=task)

        task.delete()

        assert not TaskAcknowledgement.objects.exists()

    def test_deleting_the_user_keeps_the_acknowledgement(self):
        """Who signed something off is history; losing the account shouldn't erase it, and
        shouldn't block deleting the account either."""
        user = make_real_user()
        TaskAcknowledgement.objects.create(task=make_task(), acknowledged_by=user)

        user.delete()

        acknowledgement = TaskAcknowledgement.objects.get()
        assert acknowledgement.acknowledged_by is None
        assert str(acknowledgement) == f'{TASK_ID} acknowledged by a deleted user'
