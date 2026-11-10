# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
System checks for this app's settings, so a bad value stops `manage.py check` and `migrate`
at deploy time instead of breaking the task pages and alerts at run time.
"""

from django.conf import settings
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
                f"TASK_RECENT_WINDOW_HOURS must be a whole number of hours, at least 1, not {hours!r}.",
                id="tufts_local.E001",
            )
        )
    days = failure_alert_days()
    if days is not None and not (_is_whole_number(days) and days >= 1):
        errors.append(
            checks.Error(
                f"TASK_FAILURE_ALERT_DAYS must be a whole number of days, at least 1, or None, not {days!r}.",
                hint="None counts unacknowledged failures however old they are.",
                id="tufts_local.E002",
            )
        )
    return errors


@checks.register()
def check_review_alert_settings(app_configs, **kwargs):
    days = allocation_request_alert_days()
    if not (_is_whole_number(days) and days >= 0):
        return [
            checks.Error(
                f"ALLOCATION_REQUEST_ALERT_DAYS must be a whole number of days, at least 0, not {days!r}.",
                hint="0 raises the alert for every allocation request waiting for review.",
                id="tufts_local.E003",
            )
        ]
    return []


@checks.register()
def check_view_as_dependencies(app_configs, **kwargs):
    """
    View as, configured, needs coldfront-utils 0.2.0 or later, which added task_context.su.

    The plugin's git dependency can't carry a version floor, so an older utils is possible. Run by
    `migrate` at deploy, so a mismatch stops the deploy rather than failing requests afterwards.
    """
    configured = []
    if getattr(settings, "SU_CUSTOM_LOGIN_ACTION", None) == "tufts_local.su.log_su_login":
        configured.append("SU_CUSTOM_LOGIN_ACTION")
    if "tufts_local.middleware.SuReadOnlyMiddleware" in settings.MIDDLEWARE:
        configured.append("tufts_local.middleware.SuReadOnlyMiddleware in MIDDLEWARE")
    if any(
        "tufts_local.context_processors.view_as" in engine.get("OPTIONS", {}).get("context_processors", ())
        for engine in settings.TEMPLATES
    ):
        configured.append("the tufts_local.context_processors.view_as context processor")
    if not configured:
        return []
    try:
        import coldfront_utils.task_context.su  # noqa: F401
    except ImportError:
        return [
            checks.Error(
                f"View as needs coldfront-utils 0.2.0 or later, for coldfront_utils.task_context.su, "
                f"but it can't be imported. Configured: {'; '.join(configured)}.",
                hint="Upgrade coldfront-utils in the lock (uv lock --upgrade-package coldfront-utils), "
                "or leave View as unconfigured.",
                id="tufts_local.E004",
            )
        ]
    return []
