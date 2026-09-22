# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from coldfront.config.settings import *  # noqa: F401,F403

INSTALLED_APPS = [*INSTALLED_APPS, 'tufts_local']  # noqa: F405
