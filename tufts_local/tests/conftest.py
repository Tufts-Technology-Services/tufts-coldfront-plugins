# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The stand-ins for the private Tufts packages live in stubs.py, which tests/settings.py
imports so they are in place before django.setup() populates the app registry. Importing
them here too keeps them available to any collection that doesn't go through settings.
"""

import pytest
from django.contrib.auth import get_user_model

from coldfront.core.field_of_science.models import FieldOfScience
from coldfront.core.project.models import (
    Project,
    ProjectStatusChoice,
    ProjectUser,
    ProjectUserRoleChoice,
    ProjectUserStatusChoice,
)

from tufts_local import alerts
from tufts_local.tests import stubs  # noqa: F401
from tufts_local.tests.fakes import FakeBroker


@pytest.fixture(autouse=True)
def fake_broker(monkeypatch):
    """
    Every test gets an in-memory task broker: reachable, empty, no clusters.

    The test settings name no broker, so django-q would try Redis on localhost, and redis-py
    retries a refused connection for seconds -- on every render of a page with the queue panel.
    """
    broker = FakeBroker()
    monkeypatch.setattr('tufts_local.queue_health.get_broker', lambda: broker)
    return broker


@pytest.fixture(autouse=True)
def no_cached_alerts(monkeypatch):
    """Each test starts with no alerts held from an earlier test's run of the sources."""
    monkeypatch.setattr(alerts, '_latest', None)


@pytest.fixture
def roles(db):
    return {name: ProjectUserRoleChoice.objects.get_or_create(name=name)[0] for name in ('Manager', 'User')}


@pytest.fixture
def statuses(db):
    return {name: ProjectUserStatusChoice.objects.get_or_create(name=name)[0] for name in ('Active', 'Removed')}


@pytest.fixture
def project(db, roles, statuses):
    """An active project whose PI, pi01, is also a Manager member, as ColdFront's ProjectCreateView makes them."""
    FieldOfScience.objects.get_or_create(pk=FieldOfScience.DEFAULT_PK, defaults={'description': 'Other'})
    pi = get_user_model().objects.create(username='pi01', first_name='Pat', last_name='Investigator')
    project = Project.objects.create(
        title='Ocean Modelling', pi=pi, status=ProjectStatusChoice.objects.get_or_create(name='Active')[0]
    )
    ProjectUser.objects.create(project=project, user=pi, role=roles['Manager'], status=statuses['Active'])
    return project
