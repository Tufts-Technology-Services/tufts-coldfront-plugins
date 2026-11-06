"""A new storage allocation is indexed in Starfish once its share exists, and then tagged.

Indexing used to start when the allocation was activated, alongside the share task that makes
its directory, so the first scans often ran before there was anything to find.
"""

from unittest.mock import patch

import pytest
from coldfront_utils.signals import share_creation_successful
from coldfront_utils.tasks import Rescheduled, RetryLater

from coldfront.core.allocation.models import (
    Allocation,
    AllocationAttributeType,
    AllocationStatusChoice,
    AttributeType,
)
from coldfront.core.allocation.signals import allocation_activate

from tufts_local.tasks import index_new_allocation

VOL_PATH = "tier1:/cluster/tufts/oceanlab"


@pytest.fixture
def allocation(project):
    allocation = Allocation.objects.create(
        project=project, status=AllocationStatusChoice.objects.get_or_create(name="Active")[0], justification="why"
    )
    allocation.allocationattribute_set.create(
        allocation_attribute_type=AllocationAttributeType.objects.get_or_create(
            name="sf_vol_path", defaults={"attribute_type": AttributeType.objects.get_or_create(name="Text")[0]}
        )[0],
        value=VOL_PATH,
    )
    return allocation


@pytest.fixture
def queued():
    with patch("tufts_local.signals.async_task") as async_task:
        yield async_task


@pytest.fixture
def scheduled():
    """The retries @reschedulable schedules, instead of django-q's schedule()."""
    with patch("coldfront_utils.tasks.reschedulable.schedule") as schedule:
        yield schedule


@pytest.fixture
def tagging():
    with (
        patch("tufts_local.tasks.set_sf_owner_tag") as set_sf_owner_tag,
        patch("tufts_local.tasks.update_sf_approver_tags") as update_sf_approver_tags,
    ):
        yield set_sf_owner_tag, update_sf_approver_tags


def starfish_answers(scan_id, status):
    return patch("tufts_local.tasks.add_to_starfish_index", return_value=(scan_id, status))


# --- what starts it -------------------------------------------------------------------------------


def test_a_created_share_queues_indexing(queued, allocation):
    share_creation_successful.send_robust(sender=object(), allocation=allocation, result={})

    queued.assert_called_once()
    assert queued.call_args.args == (index_new_allocation,)
    assert queued.call_args.kwargs["allocation_id"] == allocation.pk
    # the name and group it has always had, which the task report's ignore rules match on
    assert queued.call_args.kwargs["q_options"] == {
        "task_name": f"add_sf_tags_alloc_activate_{allocation.pk}",
        "group": "starfish",
    }


def test_with_owner_tags_off_a_created_share_queues_nothing(queued, allocation):
    with patch("tufts_local.signals.SF_OWNER_TAG_PERSIST", False):
        share_creation_successful.send_robust(sender=object(), allocation=allocation, result={})

    queued.assert_not_called()


def test_activating_an_allocation_no_longer_queues_indexing(queued, allocation):
    allocation_activate.send(sender=None, allocation_pk=allocation.pk)

    queued.assert_not_called()


# --- the task -------------------------------------------------------------------------------------


def test_an_indexed_path_is_tagged(allocation, scheduled, tagging):
    set_sf_owner_tag, update_sf_approver_tags = tagging

    with starfish_answers(None, True) as add_to_starfish_index:
        result = index_new_allocation(allocation_id=allocation.pk)

    add_to_starfish_index.assert_called_once_with(VOL_PATH, "starfish", None)
    set_sf_owner_tag.assert_called_once_with(allocation.pk)
    update_sf_approver_tags.assert_called_once_with(allocation.project.pk)
    scheduled.assert_not_called()
    assert result["message"].startswith("successful:")


def test_a_scan_still_running_is_checked_again_later(allocation, scheduled, tagging):
    with starfish_answers("scan-7", {"state": {"is_running": True}}):
        result = index_new_allocation(allocation_id=allocation.pk)

    assert isinstance(result, Rescheduled)
    retry = scheduled.call_args.kwargs
    assert retry["allocation_id"] == allocation.pk
    # the same scan, not a new one
    assert retry["scan_id"] == "scan-7"
    assert retry["q_options"]["task_name"] == f"add_sf_tags_alloc_activate_{allocation.pk}"
    tagging[0].assert_not_called()


def test_a_scan_that_didnt_find_the_directory_scans_again_later(allocation, scheduled, tagging):
    with starfish_answers(None, False) as add_to_starfish_index:
        result = index_new_allocation(allocation_id=allocation.pk, scan_id="scan-7")

    add_to_starfish_index.assert_called_once_with(VOL_PATH, "starfish", "scan-7")
    assert isinstance(result, Rescheduled)
    # no scan id, so the next run starts a new scan
    assert scheduled.call_args.kwargs["scan_id"] is None
    tagging[0].assert_not_called()


def test_when_retries_run_out_the_task_fails(allocation, scheduled, tagging):
    with starfish_answers(None, False), pytest.raises(RetryLater, match=VOL_PATH):
        index_new_allocation(allocation_id=allocation.pk, retries=0)

    scheduled.assert_not_called()


def test_an_allocation_without_a_vol_path_fails(allocation, scheduled):
    allocation.allocationattribute_set.all().delete()

    with pytest.raises(ValueError, match="sf_vol_path"):
        index_new_allocation(allocation_id=allocation.pk)

    scheduled.assert_not_called()
