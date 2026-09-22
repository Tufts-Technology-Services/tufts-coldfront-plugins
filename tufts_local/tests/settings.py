# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

# must come first: LocalConfig.ready() imports the private Tufts packages transitively,
# and apps.populate() runs immediately after this module is loaded
from tufts_local.tests import stubs  # noqa: F401  isort: skip

from coldfront.config.settings import *  # noqa: E402,F401,F403

INSTALLED_APPS = [*INSTALLED_APPS, 'tufts_local']  # noqa: F405
