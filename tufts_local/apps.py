# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from django.apps import AppConfig


class LocalConfig(AppConfig):
    name = 'tufts_local'

    def ready(self):
        from . import signals  # noqa: F401
