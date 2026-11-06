# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from django.apps import AppConfig, apps


class LocalConfig(AppConfig):
    name = "tufts_local"

    def ready(self):
        # importing registers: queue_health, task_failures and review_alerts their alert sources, checks its
        # system checks
        from . import checks, queue_health, review_alerts, signals, task_failures  # noqa: F401

        # coldfront_billing is optional; billing_alerts imports it
        if apps.is_installed("coldfront_billing"):
            from . import billing_alerts  # noqa: F401
