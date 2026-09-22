# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The stand-ins for the private Tufts packages live in stubs.py, which tests/settings.py
imports so they are in place before django.setup() populates the app registry. Importing
them here too keeps them available to any collection that doesn't go through settings.
"""

from tufts_local.tests import stubs  # noqa: F401
