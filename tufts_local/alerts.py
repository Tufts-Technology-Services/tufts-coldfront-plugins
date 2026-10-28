# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The admin alerts list: conditions somebody on staff should know about, gathered in one place.

Each kind of alert comes from a source, a function that takes no arguments and returns the
alerts it currently sees, usually none. Sources are live checks, so an alert goes away by itself
once its condition clears. Every staff page polls the list from the navbar, so readers go through
get_current_alerts(), which runs the sources at most once per CACHE_SECONDS in each process.
Register a source with @register_alert_source, in a module LocalConfig.ready() imports.
"""

import logging
import threading
import time
from dataclasses import dataclass, replace

from django.utils import timezone

logger = logging.getLogger(__name__)

# how long get_current_alerts() reuses one run of the sources; the navbar polls every 30s
CACHE_SECONDS = 15

# worst first: each severity is also the Bootstrap contextual colour it is shown in, mapped to its label
SEVERITY_LABELS = {'danger': 'Critical', 'warning': 'Warning', 'info': 'Info'}
SEVERITIES = tuple(SEVERITY_LABELS)


@dataclass(frozen=True)
class Alert:
    severity: str
    title: str
    detail: str = ''
    # where to go to act on it, if anywhere
    link: str = ''
    # the name it was registered under; collect_alerts() fills it in
    source: str = ''
    # where to POST to acknowledge it, for sources that offer that; the source decides what
    # acknowledging means. Empty for alerts that clear only when their cause is fixed.
    ack_url: str = ''

    def __post_init__(self):
        if self.severity not in SEVERITIES:
            raise ValueError(f'Unknown alert severity {self.severity!r}; expected one of {SEVERITIES}')

    @property
    def severity_label(self):
        return SEVERITY_LABELS[self.severity]


_alert_sources = {}

# (time.monotonic(), checked_at, alerts) from the last run, or None. Held in memory rather than in
# Django's cache, which is the same Redis server the task queue source exists to check on.
_latest = None
_latest_lock = threading.Lock()


def register_alert_source(name):
    """Register the decorated function as the alert source called `name`, shown with each of its alerts."""

    def decorator(func):
        if name in _alert_sources and _alert_sources[name] is not func:
            raise ValueError(f'An alert source called {name!r} is already registered')
        _alert_sources[name] = func
        return func

    return decorator


def get_current_alerts():
    """
    The alerts as of at most CACHE_SECONDS ago, worst first, and the time they were checked.

    Each process keeps its own copy, so however many staff tabs are polling, a process runs the
    sources at most once per interval. Requests arriving during a run wait for it, rather than
    starting their own.
    """
    global _latest
    with _latest_lock:
        if _latest is None or time.monotonic() - _latest[0] >= CACHE_SECONDS:
            _latest = (time.monotonic(), timezone.now(), collect_alerts())
        _, checked_at, alerts = _latest
    return list(alerts), checked_at


def forget_current_alerts():
    """
    Drop this process's cached alerts, so the next read runs the sources again.

    For actions that clear an alert, such as acknowledging a failed task: the person who
    acted sees it go at once. Other processes still hold theirs for up to CACHE_SECONDS.
    """
    global _latest
    with _latest_lock:
        _latest = None


def collect_alerts():
    """
    Run every registered source now and return their alerts, worst first.

    A source that raises, or returns something other than alerts, becomes a warning of its own
    instead of taking the list down with it: the sources most worth having are the ones checking
    things that might be broken. Test each source directly, since a bug in one shows up here only
    as that warning.
    """
    alerts = []
    for name, source in _alert_sources.items():
        try:
            found = [_with_source(alert, name) for alert in source()]
        except Exception as e:
            logger.exception(f'Alert source {name!r} failed')
            found = [Alert('warning', f'Could not check {name}', detail=f'{type(e).__name__}: {e}', source=name)]
        alerts.extend(found)
    # sorted() is stable, so alerts of one severity keep the order their sources gave them
    return sorted(alerts, key=lambda alert: SEVERITIES.index(alert.severity))


def _with_source(alert, name):
    if not isinstance(alert, Alert):
        raise TypeError(f'expected the source to return Alerts, got {type(alert).__name__}')
    return replace(alert, source=name)
