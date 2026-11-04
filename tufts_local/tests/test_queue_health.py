# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import time

import pytest
from django.utils import translation
from django_q.brokers import get_broker
from django_q.conf import Conf

from tufts_local.queue_health import get_task_queue_health, task_queue_alerts
from tufts_local.tests.test_task_report import get_report


class TestQueueHealth:
    def test_healthy_with_a_running_cluster(self, fake_broker):
        fake_broker.add_cluster(host='worker-host-a', workers=4)
        fake_broker.queued = 3

        health = get_task_queue_health()

        assert health.state == 'healthy'
        assert health.overall_ok
        assert health.queued == 3
        assert health.broker_info == 'Redis 7.2.4'
        assert [(c.host, c.workers) for c in health.clusters] == [('worker-host-a', 4)]

    def test_clusters_on_other_hosts_are_listed(self, fake_broker):
        fake_broker.add_cluster(host='worker-host-b')
        fake_broker.add_cluster(host='worker-host-a', status=Conf.WORKING)

        health = get_task_queue_health()

        assert [c.host for c in health.clusters] == ['worker-host-a', 'worker-host-b']
        assert health.clusters[0].severity == 'primary'

    def test_unreachable_broker(self, fake_broker):
        fake_broker.reachable = False

        health = get_task_queue_health()

        assert health.state == 'broker_down'
        assert health.severity == 'danger'
        assert 'Connection refused' in health.broker_error
        assert health.summary == 'cannot reach the task broker'

    def test_a_refused_connection_is_answered_without_retrying(self, monkeypatch):
        """The real django-q broker, not the fake: with redis-py's default retries this takes ~3.5s."""
        monkeypatch.setattr('tufts_local.queue_health.get_broker', get_broker)
        monkeypatch.setattr(Conf, 'REDIS', {'host': '127.0.0.1', 'port': 1})

        started = time.monotonic()
        health = get_task_queue_health()

        assert health.state == 'broker_down'
        assert time.monotonic() - started < 1

    def test_no_clusters(self):
        health = get_task_queue_health()

        assert health.state == 'no_workers'
        assert health.severity == 'danger'

    @pytest.mark.parametrize('status', [Conf.STARTING, Conf.STOPPING])
    def test_clusters_not_taking_tasks_are_degraded(self, fake_broker, status):
        fake_broker.add_cluster(status=status)

        health = get_task_queue_health()

        assert health.state == 'degraded'
        assert health.severity == 'warning'

    def test_a_cluster_with_no_workers_is_not_running(self, fake_broker):
        fake_broker.add_cluster(workers=0)

        assert get_task_queue_health().state == 'degraded'

    def test_a_running_cluster_is_healthy_in_any_language(self, fake_broker):
        """The status labels are translated, so the check can't hold them as the English strings."""
        fake_broker.add_cluster()

        with translation.override('de'):
            health = get_task_queue_health()

            assert health.clusters[0].status == 'Leerlauf'
            assert health.state == 'healthy'
            assert health.clusters[0].severity == 'success'

    def test_a_heartbeat_signed_with_another_secret_key_is_unseen(self, fake_broker):
        """What a remote cluster with a different SECRET_KEY looks like: it isn't there at all."""
        fake_broker.add_cluster(secret_key='not-this-servers-key')

        assert get_task_queue_health().state == 'no_workers'

    def test_summary_counts(self, fake_broker):
        fake_broker.add_cluster(workers=4)
        fake_broker.add_cluster(workers=4)
        fake_broker.queued = 1

        assert get_task_queue_health().summary == '2 clusters · 8 workers · 1 task waiting'


@pytest.mark.urls('tufts_local.tests.urls')
class TestTaskQueueAlerts:
    def test_no_alert_when_healthy(self, fake_broker):
        fake_broker.add_cluster()

        assert task_queue_alerts() == []

    def test_no_alert_when_only_degraded(self, fake_broker):
        """Amber is a cluster mid-restart, which sorts itself out; the alerts list is for red."""
        fake_broker.add_cluster(status=Conf.STARTING)

        assert task_queue_alerts() == []

    def test_alert_when_no_workers(self, fake_broker):
        fake_broker.queued = 41

        (alert,) = task_queue_alerts()

        assert alert.severity == 'danger'
        assert alert.title == 'Task queue: no workers'
        assert '41 tasks waiting' in alert.detail

    def test_alert_when_broker_down(self, fake_broker):
        fake_broker.reachable = False

        (alert,) = task_queue_alerts()

        assert alert.title == 'Task queue: broker down'
        assert 'Connection refused' in alert.detail

    def test_alert_links_to_the_task_report(self, fake_broker):
        """The queue status lives on the report now; the summary page it used to point at is gone."""
        fake_broker.reachable = False

        (alert,) = task_queue_alerts()

        assert alert.link == '/task-report/'


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestQueueIndicator:
    """The task report's one-line queue status, and the modal it opens."""

    def render_report(self, rf, htmx=False):
        response = get_report(rf, htmx=htmx)
        response.render()
        return response.content.decode()

    @staticmethod
    def indicator(content):
        return content.split('id="task-queue-indicator"', 1)[1].split('</button>', 1)[0]

    @staticmethod
    def detail(content):
        return content.split('id="task-queue-detail"', 1)[1].split('Checked', 1)[0]

    def test_green_shows_healthy_and_the_clusters_in_the_modal(self, rf, fake_broker):
        fake_broker.add_cluster(host='worker-host-a')

        content = self.render_report(rf)

        assert '<span class="badge text-bg-success">Healthy</span>' in self.indicator(content)
        assert 'border-success' in content.split('id="task-queue-indicator"', 1)[1].split('>', 1)[0]
        assert '1 cluster · 4 workers · 0 tasks waiting' in self.indicator(content)
        assert 'worker-host-a' in self.detail(content)
        assert '<span class="badge text-bg-success">Idle</span>' in self.detail(content)

    def test_the_indicator_opens_the_modal(self, rf):
        content = self.render_report(rf)

        assert 'data-bs-target="#task-queue-modal"' in self.indicator(content)
        assert content.count('id="task-queue-modal"') == 1

    def test_red_when_the_broker_is_down(self, rf, fake_broker):
        fake_broker.reachable = False

        content = self.render_report(rf)

        assert '<span class="badge text-bg-danger">Broker down</span>' in self.indicator(content)
        assert 'Could not connect to the task broker' in self.detail(content)

    def test_amber_when_degraded(self, rf, fake_broker):
        fake_broker.add_cluster(status=Conf.STOPPING)

        content = self.render_report(rf)

        assert '<span class="badge text-bg-warning">Degraded</span>' in self.indicator(content)
        assert '<div class="alert alert-warning">' in self.detail(content)

    def test_no_workers_is_explained_in_red(self, rf):
        """The autouse fake broker has no clusters. The box matches the red badge, not amber's."""
        content = self.render_report(rf)

        assert '<span class="badge text-bg-danger">No workers</span>' in self.indicator(content)
        assert '<div class="alert alert-danger mb-0">\n      No task clusters have reported' in self.detail(content)

    def test_a_full_page_renders_it_in_place(self, rf):
        """Out-of-band markers on a full page load would be inert, but would also say something untrue."""
        content = self.render_report(rf)

        assert 'hx-swap-oob' not in content

    def test_a_poll_sends_it_back_out_of_band(self, rf, fake_broker):
        """The indicator sits beside the filters, outside the polled tables, so htmx can only
        refresh it out-of-band: by id, from the same response."""
        fake_broker.add_cluster(host='worker-host-a')

        content = self.render_report(rf, htmx=True)

        assert '<button type="button" id="task-queue-indicator" hx-swap-oob="true"' in content
        assert '<div id="task-queue-detail" hx-swap-oob="true">' in content
        assert 'worker-host-a' in content
        # only the contents travel: the modal itself stays put, outside the swapped region
        assert 'id="task-queue-modal"' not in content

    def test_the_poll_pauses_while_the_modal_is_open(self, rf):
        content = self.render_report(rf)

        assert '#task-queue-modal.show' in content
