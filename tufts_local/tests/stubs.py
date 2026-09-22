# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Stand-ins for the private Tufts packages (coldfront_utils, coldfront_billing, storage)
that aren't published to a public index.

These have to be installed into sys.modules before django.setup() runs apps.populate(),
because LocalConfig.ready() imports signals -> tasks -> starfish_utils, which reaches for
them at module scope. conftest.py is too late for that, so tests/settings.py imports this
module: the settings module is loaded at the very start of django.setup(), before any app
config is readied.

When the real packages are installed (e.g. an environment with access to the private
index), every _stub_module call is a no-op and the real packages are used untouched.
"""

import importlib.util
import sys
import types


def _installed(name):
    """Is the real top-level package present?

    Checked with find_spec rather than import_module because this runs from settings.py,
    before apps.populate(): coldfront_utils imports coldfront models at package scope, so
    importing it here raises AppRegistryNotReady, which is not an ImportError and would
    escape. find_spec locates a package without executing it. Only top-level names are
    passed in, since find_spec on a dotted name imports the parent package.
    """
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _stub_module(name, requires=(), **attrs):
    """Register a stand-in for `name` unless everything in `requires` is installed.

    `requires` defaults to name's own top-level package. Pass it explicitly for a module
    that needs more than its own distribution to import -- coldfront_utils.util.ad_search
    reaches through to coldfront's ldap_user_search plugin, so it needs python-ldap even
    when coldfront_utils itself is installed.
    """
    for dependency in requires or (name.split('.')[0],):
        if not _installed(dependency):
            break
    else:
        return
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


def _ttl_cache(*args, **kwargs):
    def decorator(fn):
        return fn

    return decorator


_stub_module('coldfront_utils', ttl_cache=_ttl_cache)
_stub_module('coldfront_utils.util')
_stub_module('coldfront_utils.util.ad_search', requires=('coldfront_utils', 'ldap'), ADSearch=type('ADSearch', (), {}))

_stub_module('coldfront_billing')
_stub_module(
    'coldfront_billing.models',
    NoCostQuota=type('NoCostQuota', (), {}),
    NoCostQuotaAllotment=type('NoCostQuotaAllotment', (), {}),
)

_stub_module('storage')
_stub_module('storage.utils', get_client_config=lambda *a, **k: {})
