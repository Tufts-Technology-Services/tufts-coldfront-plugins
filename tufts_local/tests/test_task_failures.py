# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import datetime

import pytest
from django.utils import timezone

from tufts_local import alerts
from tufts_local.alerts import get_current_alerts
from tufts_local.models import IgnoredTask, TaskAcknowledgement
from tufts_local.task_failures import task_failure_alerts
from tufts_local.tests.test_task_summary import make_task

DAY = 24 * 60


def severities_and_titles():
    return [(alert.severity, alert.title) for alert in task_failure_alerts()]


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestTaskFailureAlerts:
    def test_nothing_failed_means_no_alerts(self):
        make_task('a' * 32, 'fine', minutes_ago=1)

        assert task_failure_alerts() == []

    def test_a_recent_failure_is_critical(self):
        make_task('a' * 32, 'boom', success=False, minutes_ago=1)
        make_task('b' * 32, 'boom', success=False, minutes_ago=90)

        (alert,) = task_failure_alerts()

        assert alert.severity == 'danger'
        assert alert.title == 'Background tasks: 2 failed in the last 24 hours'
        assert alert.link == '/task-report/'

    @pytest.mark.parametrize('minutes_ago', (25 * 60, 3 * DAY), ids=('yesterday', 'days_ago'))
    def test_an_earlier_failure_is_a_warning(self, minutes_ago):
        """Outside the critical window it still needs answering, so it isn't left for someone to find."""
        make_task('a' * 32, 'boom', success=False, minutes_ago=minutes_ago)

        assert severities_and_titles() == [('warning', 'Background tasks: 1 earlier failure not acknowledged')]

    def test_recent_and_earlier_failures_are_separate_alerts(self):
        make_task('a' * 32, 'boom', success=False, minutes_ago=1)
        make_task('b' * 32, 'boom', success=False, minutes_ago=25 * 60)
        make_task('c' * 32, 'boom', success=False, minutes_ago=30 * 60)

        assert severities_and_titles() == [
            ('danger', 'Background tasks: 1 failed in the last 24 hours'),
            ('warning', 'Background tasks: 2 earlier failures not acknowledged'),
        ]

    def test_the_recent_window_comes_from_the_setting(self, settings):
        settings.TASK_RECENT_WINDOW_HOURS = 2
        make_task('a' * 32, 'boom', success=False, minutes_ago=3 * 60)

        (alert,) = task_failure_alerts()

        assert alert.severity == 'warning'
        assert alert.detail.startswith('Failed more than 2 hours ago')

    def test_failures_older_than_the_limit_raise_no_alert(self):
        """Seven days by default, so failures nobody will ever answer don't keep the alert up forever."""
        make_task('a' * 32, 'boom', success=False, minutes_ago=8 * DAY)

        assert task_failure_alerts() == []

    def test_the_limit_comes_from_the_setting(self, settings):
        settings.TASK_FAILURE_ALERT_DAYS = 30
        make_task('a' * 32, 'boom', success=False, minutes_ago=8 * DAY)

        (alert,) = task_failure_alerts()

        assert alert.severity == 'warning'
        assert 'within the last 30 days' in alert.detail

    def test_no_limit_when_the_setting_is_none(self, settings):
        settings.TASK_FAILURE_ALERT_DAYS = None
        make_task('a' * 32, 'boom', success=False, minutes_ago=400 * DAY)

        (alert,) = task_failure_alerts()

        assert alert.detail.startswith('Failed more than 24 hours ago. ')
        assert 'since=' not in alert.ack_url

    @pytest.mark.parametrize('minutes_ago', (1, 25 * 60), ids=('recent', 'earlier'))
    def test_acknowledging_a_failure_clears_its_alert(self, minutes_ago):
        task = make_task('a' * 32, 'boom', success=False, minutes_ago=minutes_ago)
        assert task_failure_alerts() != []

        TaskAcknowledgement.objects.create(task=task)

        assert task_failure_alerts() == []

    @pytest.mark.parametrize(
        'field, pattern', [(IgnoredTask.NAME, 'add_sf_tags'), (IgnoredTask.GROUP, 'starfish')], ids=('name', 'group')
    )
    def test_ignored_tasks_raise_no_alert(self, field, pattern):
        IgnoredTask.objects.create(field=field, pattern=pattern)
        make_task('a' * 32, 'add_sf_tags_alloc_activate_7', group='starfish', success=False, minutes_ago=1)

        assert task_failure_alerts() == []

    def test_alerts_name_no_tasks(self):
        """A task's name, arguments or result can hold anything; the alert carries counts only."""
        make_task('a' * 32, 'secret_task_name', success=False, minutes_ago=1, result='secret result')
        make_task('b' * 32, 'secret_task_name', success=False, minutes_ago=25 * 60, result='secret result')

        assert all('secret' not in f'{alert.title} {alert.detail}' for alert in task_failure_alerts())


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestAcknowledgingFromAnAlert:
    """Each alert's Acknowledge button covers exactly the failures that alert counted."""

    @pytest.fixture
    def staff_client(self, client, django_user_model):
        client.force_login(django_user_model.objects.create(username='rdms_admin', is_staff=True))
        return client

    def alert_with_severity(self, severity):
        return next(alert for alert in task_failure_alerts() if alert.severity == severity)

    def test_the_recent_alert_acknowledges_only_recent_failures(self, staff_client):
        make_task('a' * 32, 'boom', success=False, minutes_ago=1)
        make_task('b' * 32, 'boom', success=False, minutes_ago=25 * 60)

        response = staff_client.post(self.alert_with_severity('danger').ack_url, {'reason': 'known Starfish outage'})

        assert response.status_code == 302
        assert set(TaskAcknowledgement.objects.values_list('task_id', 'reason')) == {
            ('a' * 32, 'known Starfish outage')
        }
        assert severities_and_titles() == [('warning', 'Background tasks: 1 earlier failure not acknowledged')]

    def test_the_earlier_alert_acknowledges_only_its_window(self, staff_client):
        make_task('a' * 32, 'boom', success=False, minutes_ago=1)
        make_task('b' * 32, 'boom', success=False, minutes_ago=25 * 60)
        make_task('c' * 32, 'boom', success=False, minutes_ago=8 * DAY)

        staff_client.post(self.alert_with_severity('warning').ack_url)

        assert set(TaskAcknowledgement.objects.values_list('task_id', flat=True)) == {'b' * 32}

    def test_a_failure_after_the_alert_was_checked_is_left_alone(self, staff_client):
        make_task('a' * 32, 'boom', success=False, minutes_ago=1)
        ack_url = self.alert_with_severity('danger').ack_url
        # arrives after the check, so the alert never counted it
        later = make_task('b' * 32, 'boom', success=False, minutes_ago=1)
        later.stopped = timezone.now() + datetime.timedelta(seconds=1)
        later.save()

        staff_client.post(ack_url)

        assert set(TaskAcknowledgement.objects.values_list('task_id', flat=True)) == {'a' * 32}

    def test_successes_and_ignored_tasks_are_not_acknowledged(self, staff_client):
        IgnoredTask.objects.create(field=IgnoredTask.NAME, pattern='add_sf_tags')
        make_task('a' * 32, 'fine', minutes_ago=1)
        make_task('b' * 32, 'add_sf_tags_alloc_activate_7', success=False, minutes_ago=1)
        make_task('c' * 32, 'boom', success=False, minutes_ago=1)

        staff_client.post(self.alert_with_severity('danger').ack_url)

        assert set(TaskAcknowledgement.objects.values_list('task_id', flat=True)) == {'c' * 32}

    def test_the_alert_goes_at_once_for_the_person_who_acted(self, staff_client):
        make_task('a' * 32, 'boom', success=False, minutes_ago=1)
        assert get_current_alerts()[0] != []

        staff_client.post(self.alert_with_severity('danger').ack_url)

        assert [alert.source for alert in get_current_alerts()[0]] == ['task queue']


def test_registered_as_an_alert_source():
    assert alerts._alert_sources['background tasks'] is task_failure_alerts
