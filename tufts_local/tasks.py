# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import csv
import datetime
import io
import logging

from coldfront_utils import mail_report
from coldfront_utils.tasks import RetryLater, build_task_result, reschedulable
from storage.truenas import get_truenas_client

from coldfront.core.allocation.models import Allocation, AllocationAttribute
from coldfront.core.project.models import Project, ProjectUser
from coldfront.core.resource.models import Resource

from tufts_local.starfish_utils import (
    add_to_starfish_index,
    get_starfish_usage_data_by_volume,
    get_starfish_volumes,
    set_owner_tag,
    set_project_approvers_from_starfish,
    sync_approver_tags,
)

logger = logging.getLogger(__name__)

# TrueNAS reports dataset mountpoints under the pool it serves them from; the report is
# read against paths as clients see them, so the pool prefix comes back off.
TRUENAS_MOUNT_PREFIX = "/mnt/tank"

# the fields truenas_utils' get_all_datasets() returns for every dataset
TIER2_REPORT_COLUMNS = ("mountpoint", "used", "quota", "snapshot_size")


def update_project_approvers_from_tags():
    for volume in get_starfish_volumes("starfish"):
        subfolder_response = get_starfish_usage_data_by_volume(volume, "starfish")
        for vol_path_data in subfolder_response:
            set_project_approvers_from_starfish(vol_path_data)


def update_sf_approver_tags(project_id):
    # write starfish tags corresponding to project approvers for all projects in Coldfront
    updates = []
    proj = Project.objects.get(id=project_id)
    approvers = set(
        ProjectUser.objects.filter(project=proj, role__name="Manager").values_list("user__username", flat=True)
    )
    # don't include the project PI in the list of approvers
    approvers = approvers - {proj.pi.username}

    alloc_attr = AllocationAttribute.objects.filter(
        allocation__project=proj, allocation_attribute_type__name="sf_vol_path"
    )
    for attr in alloc_attr:
        vol_path = attr.value
        update = sync_approver_tags(vol_path, list(approvers), "starfish")
        updates.append(update)
    return updates


def update_sf_owner_tags_from_allocations():
    # write starfish tag corresponding to project owner for all allocations in Coldfront
    storage = Resource.objects.filter(resource_type__name="Storage")
    allocations = Allocation.objects.filter(status__name="Active", resources__in=storage).prefetch_related(
        "allocationattribute_set", "project__pi"
    )
    for alloc in allocations:
        _set_sf_owner_tag(alloc)


def set_sf_owner_tag(allocation_id):
    # write starfish tag corresponding to project owner for all allocations in Coldfront
    storage = Resource.objects.filter(resource_type__name="Storage")
    alloc_set = Allocation.objects.filter(id=allocation_id, resources__in=storage).prefetch_related(
        "allocationattribute_set", "project__pi"
    )
    if not alloc_set.exists():
        logger.debug(f"Allocation {allocation_id} does not have a storage resource. Skipping Starfish 'Owner' tagging.")
        return
    alloc = alloc_set.first()
    _set_sf_owner_tag(alloc)


def _set_sf_owner_tag(allocation):
    owner = allocation.project.pi.username
    alloc_attr = allocation.allocationattribute_set.filter(allocation_attribute_type__name="sf_vol_path").first()
    if alloc_attr:
        vol_path = alloc_attr.value
        logger.debug(
            f"Setting Starfish 'Owner' tag for allocation {allocation.id} with vol_path {vol_path} to {owner}."
        )
        set_owner_tag("starfish", vol_path, owner)
    else:
        logger.error(
            f"Allocation {allocation.id} does not have an 'sf_vol_path' attribute. Cannot set Starfish 'Owner' tag."
        )
        raise ValueError(
            f"Allocation {allocation.id} does not have an 'sf_vol_path' attribute. Cannot set Starfish 'Owner' tag."
        )


def index_task_options(allocation_id, **_):
    """django-q options for every run of index_new_allocation, its retries included.

    The task name is the one it had when activation started it: the task report's ignore rules
    match on it.
    """
    return {"task_name": f"add_sf_tags_alloc_activate_{allocation_id}", "group": "starfish"}


@reschedulable(retries=5, wait=5, q_options=index_task_options)
def index_new_allocation(allocation_id, scan_id=None):
    """Index a new allocation's directory in Starfish, then set its Owner and Approver tags.

    Queued once the allocation's share exists (signals.py). A directory Starfish hasn't indexed
    gets a shallow scan. A scan still running, or one that finished without finding the directory,
    makes the task run again in 5 minutes, up to 5 times (@reschedulable): with the same scan while
    it runs, and with a new scan after one that found nothing.
    """
    allocation = Allocation.objects.get(id=allocation_id)
    vol_path_attr = allocation.allocationattribute_set.filter(allocation_attribute_type__name="sf_vol_path")
    if not vol_path_attr.exists():
        raise ValueError(
            f"Allocation {allocation_id} does not have an 'sf_vol_path' attribute. Cannot index in Starfish."
        )
    vol_path = vol_path_attr.first().value

    logger.info(f"Indexing allocation {allocation_id}'s {vol_path} in Starfish.")
    scan_id, status = add_to_starfish_index(vol_path, "starfish", scan_id)
    if status is not True:
        if scan_id is not None:
            raise RetryLater(f"Starfish's scan {scan_id} of {vol_path} is still running", scan_id=scan_id)
        raise RetryLater(f"{vol_path} isn't in Starfish's index after a scan", scan_id=None)

    logger.info(f"{vol_path} is in Starfish's index. Setting its tags.")
    try:
        set_sf_owner_tag(allocation_id)
        update_sf_approver_tags(allocation.project.id)
    except Exception as e:
        logger.error(f"Error setting Starfish tags for allocation {allocation_id}'s {vol_path}: {e}")
        return build_task_result("successful", f"indexed {vol_path}, but setting its tags failed: {e}")
    return build_task_result("successful", f"indexed and tagged {vol_path}")


def send_tier2_quota_report(client_config_id):
    """Mail the data engineer a CSV of the quotas TrueNAS reports for its datasets.

    Datasets with no quota set are left out: TrueNAS reports those with a null refquota,
    and a row claiming a quota of 0 would read as "allotted nothing" rather than
    "nothing allotted".

    Returns a one-line summary rather than the report itself, since the return value is
    what django-q stores as the task result and shows on the task report page.
    """
    client = get_truenas_client(client_config_id)
    datasets = [dataset for dataset in client.get_all_datasets() if dataset.get("quota") is not None]
    report_date = datetime.date.today().isoformat()

    # the known fields are named so the header is the same shape even on a day TrueNAS
    # reports nothing, and anything added upstream is appended rather than dropped
    reported = dict.fromkeys(key for dataset in datasets for key in dataset)
    columns = [*TIER2_REPORT_COLUMNS, *(key for key in reported if key not in TIER2_REPORT_COLUMNS), "report_date"]

    with io.StringIO(newline="") as buffer:
        writer = csv.DictWriter(buffer, fieldnames=columns, restval="", lineterminator="\n")
        writer.writeheader()
        for dataset in datasets:
            writer.writerow(
                {
                    **dataset,
                    "mountpoint": dataset["mountpoint"].replace(TRUENAS_MOUNT_PREFIX, ""),
                    "quota": int(dataset["quota"]),
                    "report_date": report_date,
                }
            )
        report = buffer.getvalue()

    return mail_report(
        title="Tier 2 quota report",
        filename=f"tier2_quota_report_{report_date}.csv",
        content=report,
        mimetype="text/csv",
        report_date=report_date,
        body=f"Quotas TrueNAS reported for {len(datasets)} datasets on {report_date}, attached as CSV.",
        summary=f"{len(datasets)} datasets",
    )
