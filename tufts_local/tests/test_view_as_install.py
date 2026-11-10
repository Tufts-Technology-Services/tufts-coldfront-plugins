# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Installing "View as" (tufts_local.su, its middleware and context processor) on a site whose
coldfront-utils is older than 0.2.0, which added coldfront_utils.task_context.su.

The plugin's git dependency on coldfront-utils can't carry a version floor, so nothing stops a lock
pairing this plugin with an older utils. Two things make that safe: nothing the site already loads
needs the new module just to import, and a system check stops `migrate` when View as is configured
without it.
"""

import importlib
import sys

import pytest

import tufts_local.middleware
import tufts_local.su
import tufts_local.views.view_as
from tufts_local.checks import check_view_as_dependencies

SU_MODULE = "coldfront_utils.task_context.su"
VIEW_AS_PROCESSOR = "tufts_local.context_processors.view_as"
MODULES_THAT_IMPORT_AT_STARTUP = (tufts_local.su, tufts_local.middleware, tufts_local.views.view_as)


@pytest.fixture
def old_coldfront_utils(monkeypatch):
    """Make importing coldfront_utils.task_context.su fail, as it does with coldfront-utils 0.1.0."""
    monkeypatch.setitem(sys.modules, SU_MODULE, None)
    yield
    monkeypatch.undo()
    for module in MODULES_THAT_IMPORT_AT_STARTUP:
        importlib.reload(module)


@pytest.mark.usefixtures("old_coldfront_utils")
def test_the_middleware_the_site_already_loads_still_imports():
    """HtmxLoginRefreshMiddleware is in production's MIDDLEWARE: if its module failed, every request would."""
    for module in MODULES_THAT_IMPORT_AT_STARTUP:
        importlib.reload(module)

    assert tufts_local.middleware.HtmxLoginRefreshMiddleware


def configure_view_as(settings, *, action=False, middleware=False, context_processor=False):
    if action:
        settings.SU_CUSTOM_LOGIN_ACTION = "tufts_local.su.log_su_login"
    if middleware:
        settings.MIDDLEWARE = [*settings.MIDDLEWARE, "tufts_local.middleware.SuReadOnlyMiddleware"]
    if context_processor:
        # a new TEMPLATES, not an edit inside it: the settings fixture only puts back what is assigned
        first, *rest = settings.TEMPLATES
        options = {
            **first["OPTIONS"],
            "context_processors": [*first["OPTIONS"]["context_processors"], VIEW_AS_PROCESSOR],
        }
        settings.TEMPLATES = [{**first, "OPTIONS": options}, *rest]


def error_ids():
    return [error.id for error in check_view_as_dependencies(None)]


@pytest.mark.usefixtures("old_coldfront_utils")
@pytest.mark.parametrize("configured", [{"action": True}, {"middleware": True}, {"context_processor": True}])
def test_configuring_any_part_without_the_new_coldfront_utils_is_an_error(settings, configured):
    configure_view_as(settings, **configured)

    assert error_ids() == ["tufts_local.E004"]


@pytest.mark.usefixtures("old_coldfront_utils")
def test_the_error_says_which_coldfront_utils_is_needed(settings):
    configure_view_as(settings, middleware=True)

    [error] = check_view_as_dependencies(None)

    assert "coldfront-utils" in error.msg
    assert "0.2.0" in error.msg


@pytest.mark.usefixtures("old_coldfront_utils")
def test_a_site_that_doesnt_use_view_as_needs_nothing_new(settings):
    assert error_ids() == []


def test_configured_with_the_new_coldfront_utils_passes(settings):
    configure_view_as(settings, action=True, middleware=True, context_processor=True)

    assert error_ids() == []
