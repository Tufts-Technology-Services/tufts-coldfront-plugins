# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Whether django-q can run background tasks: is the broker reachable, and is any cluster working it.

Every running qcluster writes a signed heartbeat into the broker under django_q:<name>:cluster:<id>,
refreshed about twice a second and expiring after 3, so it is read from wherever the broker is
rather than from this machine. A cluster on another host shows up here as long as it shares this
server's broker, Q_CLUSTER name and SECRET_KEY. A heartbeat signed with a different SECRET_KEY is
skipped by Stat.get_all() without a word, so that cluster looks exactly like one that isn't running.
"""

import datetime
import logging
from dataclasses import dataclass, field

import redis
from django.template.defaultfilters import pluralize
from django.urls import reverse
from django_q.brokers import get_broker
from django_q.conf import Conf
from django_q.status import Stat
from redis.backoff import NoBackoff
from redis.retry import Retry

from tufts_local.alerts import Alert, register_alert_source

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClusterStatus:
    host: str
    cluster_id: str
    status: str
    workers: int
    started: datetime.datetime | None

    @property
    def is_running(self):
        """In a state whose workers take tasks off the queue, compared at call time like severity."""
        return self.status in (str(Conf.IDLE), str(Conf.WORKING)) and self.workers > 0

    @property
    def severity(self):
        """
        Bootstrap colour for the status badge, decided here because the labels are translated.

        status holds the label in the language active when the heartbeat was read, so compare it
        with labels translated now, in the same request, not once at import.
        """
        return {str(Conf.WORKING): 'primary', str(Conf.IDLE): 'success'}.get(self.status, 'warning')


# each state, worst first, with its label and the Bootstrap contextual colour alerts also use
_STATES = {
    'broker_down': ('Broker down', 'danger'),
    'no_workers': ('No workers', 'danger'),
    'degraded': ('Degraded', 'warning'),
    'healthy': ('Healthy', 'success'),
}


@dataclass(frozen=True)
class QueueHealth:
    broker_ok: bool
    broker_error: str = ''
    broker_info: str = ''
    queued: int | None = None
    clusters: list = field(default_factory=list)

    @property
    def state(self):
        """One of the keys of _STATES."""
        if not self.broker_ok:
            return 'broker_down'
        if not self.clusters:
            return 'no_workers'
        if not any(cluster.is_running for cluster in self.clusters):
            # clusters exist but none is taking tasks: starting up, or on the way down
            return 'degraded'
        return 'healthy'

    @property
    def overall_ok(self):
        return self.state == 'healthy'

    @property
    def label(self):
        return _STATES[self.state][0]

    @property
    def severity(self):
        return _STATES[self.state][1]

    @property
    def summary(self):
        """The one line shown next to the badge, so a collapsed panel still says something."""
        if not self.broker_ok:
            return 'cannot reach the task broker'
        clusters = len(self.clusters)
        workers = sum(cluster.workers for cluster in self.clusters)
        parts = [f'{clusters} cluster{pluralize(clusters)}']
        if self.clusters:
            parts.append(f'{workers} worker{pluralize(workers)}')
        parts.append(f'{self.queued} task{pluralize(self.queued)} waiting')
        return ' · '.join(parts)


def get_task_queue_health():
    """
    Check the broker and read every cluster's heartbeat from it.

    Never raises: a broker that can't be reached is the answer, not an error. It also answers in
    one attempt (see _get_broker_without_retries), but a Redis host that drops packets instead of
    refusing the connection still blocks that attempt until it times out, so Q_CLUSTER's redis
    settings want a socket_connect_timeout.
    """
    try:
        broker = _get_broker_without_retries()
        # the first command doubles as the reachability check: no separate ping, which would cost a
        # round trip and have django-q log an error of its own on every failed check
        queued = broker.queue_size()
        broker_info = str(broker.info() or '')
        stats = Stat.get_all(broker=broker)
    except Exception as e:
        logger.warning(f'Task broker health check failed: {type(e).__name__}: {e}')
        return QueueHealth(broker_ok=False, broker_error=f'{type(e).__name__}: {e}')

    clusters = [
        ClusterStatus(
            host=stat.host,
            cluster_id=str(stat.cluster_id),
            status=str(stat.status),
            workers=len(stat.workers),
            started=stat.tob,
        )
        for stat in stats
    ]
    clusters.sort(key=lambda cluster: (cluster.host, cluster.cluster_id))
    return QueueHealth(broker_ok=True, broker_info=broker_info, queued=queued, clusters=clusters)


def _get_broker_without_retries():
    """
    A broker whose Redis client gives up after the first failed attempt.

    redis-py retries a failed command 10 times by default, so a dead broker -- the one state this
    check exists to report -- would make every navbar poll wait out 11 connection attempts. The
    client is this broker's own: get_broker() builds a new one each call, so enqueueing keeps its
    retries. Not with django_redis, whose client comes from a pool the rest of the site shares.
    """
    broker = get_broker()
    if isinstance(getattr(broker, 'connection', None), redis.Redis) and not Conf.DJANGO_REDIS:
        broker.connection.set_retry(Retry(NoBackoff(), 0))
    return broker


@register_alert_source('task queue')
def task_queue_alerts():
    """Raise an alert only for the red states: amber is a cluster mid-restart, which sorts itself out."""
    health = get_task_queue_health()
    if health.severity != 'danger':
        return []
    if health.state == 'broker_down':
        detail = f'Background tasks cannot be queued or run. {health.broker_error}'
    else:
        detail = (
            f'No task cluster has reported in the last few seconds, so {health.queued} '
            f'task{pluralize(health.queued)} waiting will not run. A cluster on another host also goes unseen if its Q_CLUSTER name or '
            'SECRET_KEY differs from this server.'
        )
    return [Alert('danger', f'Task queue: {health.label.lower()}', detail=detail, link=reverse('task-summary'))]
