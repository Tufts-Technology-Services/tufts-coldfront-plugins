# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from django.apps import AppConfig


class LocalConfig(AppConfig):
    name = 'tufts_local'

    def ready(self):
        # importing registers: queue_health and task_failures their alert sources, checks its system checks
        from . import checks, queue_health, signals, task_failures  # noqa: F401
