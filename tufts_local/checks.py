# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
System checks for this app's settings, so a bad value stops `manage.py check` and `migrate`
at deploy time instead of breaking the task pages and alerts at run time.
"""

from django.core import checks

from tufts_local.review_alerts import allocation_request_alert_days
from tufts_local.task_failures import failure_alert_days, recent_window_hours


def _is_whole_number(value):
    # bool is an int subclass, and True would otherwise pass as 1
    return isinstance(value, int) and not isinstance(value, bool)


@checks.register()
def check_task_settings(app_configs, **kwargs):
    errors = []
    hours = recent_window_hours()
    if not (_is_whole_number(hours) and hours >= 1):
        errors.append(
            checks.Error(
                f'TASK_RECENT_WINDOW_HOURS must be a whole number of hours, at least 1, not {hours!r}.',
                id='tufts_local.E001',
            )
        )
    days = failure_alert_days()
    if days is not None and not (_is_whole_number(days) and days >= 1):
        errors.append(
            checks.Error(
                f'TASK_FAILURE_ALERT_DAYS must be a whole number of days, at least 1, or None, not {days!r}.',
                hint='None counts unacknowledged failures however old they are.',
                id='tufts_local.E002',
            )
        )
    return errors


@checks.register()
def check_review_alert_settings(app_configs, **kwargs):
    days = allocation_request_alert_days()
    if not (_is_whole_number(days) and days >= 0):
        return [
            checks.Error(
                f'ALLOCATION_REQUEST_ALERT_DAYS must be a whole number of days, at least 0, not {days!r}.',
                hint='0 raises the alert for every allocation request waiting for review.',
                id='tufts_local.E003',
            )
        ]
    return []
