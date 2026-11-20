# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Deleting an allocation records its resources in history, and an Active one can't be deleted.

Mistaken allocations (created on the wrong project, or withdrawn by the requester) are deleted
rather than given a status, to hide them from end users. The allocation's history doesn't track its
resources, so the deletion row's change reason carries them. An Active allocation has to be revoked
first, so its users are taken off it: a delete sends none of the signals that does that.
"""

import pytest
from django.contrib.auth import get_user_model
from django.db import transaction

from coldfront.core.allocation.models import Allocation, AllocationStatusChoice
from coldfront.core.resource.models import Resource, ResourceType

from tufts_local.signals import ActiveAllocationDeletion


def make_resource(name):
    resource_type = ResourceType.objects.get_or_create(name="Storage")[0]
    return Resource.objects.create(name=name, resource_type=resource_type, description=name)


def make_allocation(project, status, *resources):
    allocation = Allocation.objects.create(
        project=project, status=AllocationStatusChoice.objects.get_or_create(name=status)[0], justification="why"
    )
    allocation.resources.add(*resources)
    return allocation


def refused(callable_):
    """Run a delete that should be refused, inside a savepoint, as a request or shell delete is in its own transaction.

    The delete runs in atomic(savepoint=False), so without one, the refusal would leave the test's own
    transaction unusable.
    """
    with pytest.raises(ActiveAllocationDeletion, match="revoke it first"), transaction.atomic():
        callable_()


def deletion_reason(allocation_pk):
    return Allocation.history.get(id=allocation_pk, history_type="-").history_change_reason


def test_deleting_records_the_resources(project):
    allocation = make_allocation(project, "New", make_resource("Tier 1"))
    pk = allocation.pk

    allocation.delete()

    assert deletion_reason(pk) == "Deleted; resources: Tier 1"


def test_several_resources_are_listed_by_name(project):
    allocation = make_allocation(project, "New", make_resource("Tier 2"), make_resource("HPC Cluster"))
    pk = allocation.pk

    allocation.delete()

    assert deletion_reason(pk) == "Deleted; resources: HPC Cluster, Tier 2"


def test_an_allocation_without_resources_says_so(project):
    allocation = make_allocation(project, "New")
    pk = allocation.pk

    allocation.delete()

    assert deletion_reason(pk) == "Deleted; resources: none"


def test_a_long_list_is_cut_to_fit_the_reason_field(project):
    resources = [make_resource(f"Storage Resource Number {n:02}") for n in range(6)]
    allocation = make_allocation(project, "New", *resources)
    pk = allocation.pk

    allocation.delete()

    reason = deletion_reason(pk)
    assert len(reason) == 100
    assert reason.startswith("Deleted; resources: Storage Resource Number 00, ")
    assert reason.endswith("…")


def test_a_queryset_delete_records_each_allocation(project):
    first = make_allocation(project, "Denied", make_resource("Tier 1"))
    second = make_allocation(project, "New", make_resource("Tier 2"))
    pks = (first.pk, second.pk)

    Allocation.objects.filter(pk__in=pks).delete()

    assert deletion_reason(pks[0]) == "Deleted; resources: Tier 1"
    assert deletion_reason(pks[1]) == "Deleted; resources: Tier 2"


def test_an_active_allocation_cannot_be_deleted(project):
    allocation = make_allocation(project, "Active", make_resource("Tier 1"))

    refused(allocation.delete)

    assert Allocation.objects.filter(pk=allocation.pk).exists()
    assert not Allocation.history.filter(id=allocation.pk, history_type="-").exists()


def test_a_queryset_delete_with_an_active_allocation_deletes_nothing(project):
    new = make_allocation(project, "New", make_resource("Tier 1"))
    active = make_allocation(project, "Active", make_resource("Tier 2"))

    refused(Allocation.objects.filter(pk__in=(new.pk, active.pk)).delete)

    assert Allocation.objects.filter(pk__in=(new.pk, active.pk)).count() == 2


def test_deleting_a_project_with_an_active_allocation_is_refused(project):
    allocation = make_allocation(project, "Active", make_resource("Tier 1"))

    refused(project.delete)

    assert Allocation.objects.filter(pk=allocation.pk).exists()


@pytest.mark.urls("tufts_local.tests.urls")
def test_the_admin_answers_a_refused_delete_with_403(client, project):
    allocation = make_allocation(project, "Active", make_resource("Tier 1"))
    admin = get_user_model().objects.create_superuser(username="admin01", password="pw")
    client.force_login(admin)

    response = client.post(f"/admin/allocation/allocation/{allocation.pk}/delete/", {"post": "yes"})

    assert response.status_code == 403
    assert Allocation.objects.filter(pk=allocation.pk).exists()


@pytest.mark.urls("tufts_local.tests.urls")
def test_the_admin_deletes_a_new_allocation_and_records_who(client, project):
    allocation = make_allocation(project, "New", make_resource("Tier 1"))
    pk = allocation.pk
    admin = get_user_model().objects.create_superuser(username="admin01", password="pw")
    client.force_login(admin)

    response = client.post(f"/admin/allocation/allocation/{pk}/delete/", {"post": "yes"})

    assert response.status_code == 302
    record = Allocation.history.get(id=pk, history_type="-")
    assert record.history_change_reason == "Deleted; resources: Tier 1"
    assert record.history_user == admin
