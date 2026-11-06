# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import importlib.util

# must come first: LocalConfig.ready() imports the private Tufts packages transitively,
# and apps.populate() runs immediately after this module is loaded
from tufts_local.tests import stubs  # noqa: F401  isort: skip

from coldfront.config.settings import *  # noqa: E402,F401,F403

INSTALLED_APPS = [*INSTALLED_APPS, "tufts_local"]  # noqa: F405

# In memory, whatever DB_URL says: ColdFront's settings take the database from it, and in a shell
# where it names a real server, the tests would create their database there.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# a deliberately weak hasher: creating users is much faster, and nothing here checks its strength
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# coldfront_billing is a private package, so it may not be installable (e.g. in CI). Where it
# is, it's installed as an app as it is in production, which billing_tasks' tests need: its
# models can't be imported otherwise. Where it isn't, those tests are skipped.
BILLING_INSTALLED = importlib.util.find_spec("coldfront_billing") is not None
if BILLING_INSTALLED:
    INSTALLED_APPS = [*INSTALLED_APPS, "coldfront_billing"]
