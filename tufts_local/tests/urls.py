# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Combined urlconf for tests that need to fully render a tufts_local template: the
common/base.html navbar reverses coldfront core url names, while tufts_local's own
templates reverse tufts_local url names, but ROOT_URLCONF in production only exposes
the coldfront core urlconf directly. Use `@pytest.mark.urls('tufts_local.tests.urls')`
for tests that call response.render() on a tufts_local view.
"""

from django.urls import include, path

urlpatterns = [
    path('', include('coldfront.config.urls')),
    path('', include('tufts_local.urls')),
]
