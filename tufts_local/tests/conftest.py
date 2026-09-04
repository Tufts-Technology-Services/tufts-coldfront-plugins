"""
tufts_local/views/__init__.py eagerly imports every sibling view module, which
transitively pulls in private Tufts packages (coldfront_utils, coldfront_billing,
storage) that aren't published to a public index. Those packages are unrelated
to the views under test here, so if they aren't installed we stand up minimal
stand-ins just so the import chain resolves. When the real packages are
installed (e.g. in an environment with access to the private index), this is a
no-op and the real packages are used untouched.
"""

import importlib
import sys
import types


def _stub_module(name, **attrs):
    try:
        importlib.import_module(name)
        return
    except ImportError:
        pass
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
_stub_module('coldfront_utils.util.ad_search', ADSearch=type('ADSearch', (), {}))

_stub_module('coldfront_billing')
_stub_module('coldfront_billing.constants', BILLING_ATTRIBUTE_NAME='billing_id')
_stub_module('coldfront_billing.data')
_stub_module('coldfront_billing.data.billing', create_billing_allocations=lambda *a, **k: [])
_stub_module(
    'coldfront_billing.models',
    NoCostQuota=type('NoCostQuota', (), {}),
    NoCostQuotaAllotment=type('NoCostQuotaAllotment', (), {}),
)
_stub_module('coldfront_billing.prefetch', get_projects_prefetch=lambda *a, **k: None)
_stub_module('coldfront_billing.reports')
_stub_module('coldfront_billing.reports.quota', auto_assign_quota=lambda *a, **k: None)
_stub_module('coldfront_billing.views')
_stub_module('coldfront_billing.views.no_cost_quota')
_stub_module('coldfront_billing.views.no_cost_quota.common', quota_with_remaining=lambda *a, **k: None)

_stub_module('storage')
_stub_module('storage.utils', get_client_config=lambda *a, **k: {})
