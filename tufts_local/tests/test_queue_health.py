# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import time

import pytest
from django.utils import translation
from django_q.brokers import get_broker
from django_q.conf import Conf

from tufts_local.queue_health import get_task_queue_health, task_queue_alerts
from tufts_local.tests.test_task_summary import get_summary


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


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestQueuePanel:
    def render_summary(self, rf):
        response = get_summary(rf)
        response.render()
        return response.content.decode()

    def test_collapsed_when_green(self, rf, fake_broker):
        fake_broker.add_cluster(host='worker-host-a')

        content = self.render_summary(rf)

        assert 'id="task-queue-status" class="collapse"' in content
        assert 'aria-expanded="false"' in content
        assert '<span class="badge text-bg-success">Healthy</span>' in content
        # the cluster rows are still on the page, just folded away
        assert 'worker-host-a' in content
        assert '<span class="badge text-bg-success">Idle</span>' in content

    def test_open_when_red(self, rf, fake_broker):
        fake_broker.reachable = False

        content = self.render_summary(rf)

        assert 'id="task-queue-status" class="collapse show"' in content
        assert 'aria-expanded="true"' in content
        assert '<span class="badge text-bg-danger">Broker down</span>' in content
        assert '<div class="alert alert-danger">\n    Could not connect to the task broker' in content

    def test_open_when_amber(self, rf, fake_broker):
        fake_broker.add_cluster(status=Conf.STOPPING)

        content = self.render_summary(rf)

        assert 'id="task-queue-status" class="collapse show"' in content
        assert '<span class="badge text-bg-warning">Degraded</span>' in content
        assert '<div class="alert alert-warning">' in content

    def test_no_workers_is_explained_in_red(self, rf):
        """The autouse fake broker has no clusters. The box matches the red badge, not amber's."""
        content = self.render_summary(rf)

        assert '<span class="badge text-bg-danger">No workers</span>' in content
        assert '<div class="alert alert-danger">\n    No task clusters have reported' in content
