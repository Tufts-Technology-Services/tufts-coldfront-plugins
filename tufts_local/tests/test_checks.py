# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

from tufts_local.checks import check_task_settings


def error_ids():
    return [error.id for error in check_task_settings(None)]


def test_the_defaults_pass():
    assert error_ids() == []


@pytest.mark.parametrize('hours', [1, 24, 72])
def test_recent_window_in_range(settings, hours):
    settings.TASK_RECENT_WINDOW_HOURS = hours

    assert error_ids() == []


@pytest.mark.parametrize('hours', [0, -1, 2.5, '24', True, None])
def test_recent_window_out_of_range(settings, hours):
    settings.TASK_RECENT_WINDOW_HOURS = hours

    assert error_ids() == ['tufts_local.E001']


@pytest.mark.parametrize('days', [1, 7, 365, None])
def test_failure_alert_days_allowed(settings, days):
    settings.TASK_FAILURE_ALERT_DAYS = days

    assert error_ids() == []


@pytest.mark.parametrize('days', [0, -1, 1.5, '7', False])
def test_failure_alert_days_rejected(settings, days):
    settings.TASK_FAILURE_ALERT_DAYS = days

    assert error_ids() == ['tufts_local.E002']
