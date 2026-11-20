# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging

from coldfront_utils.signals import share_creation_successful
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver
from django_q.tasks import async_task

from coldfront.core.allocation.models import Allocation
from coldfront.core.project.models import ProjectUser

from .constants import SF_OWNER_TAG_PERSIST
from .starfish_utils import writing_roles_from_starfish
from .tasks import index_new_allocation, index_task_options, update_sf_approver_tags

logger = logging.getLogger(__name__)


@receiver(share_creation_successful, dispatch_uid="tufts_local.index_allocation_once_its_share_exists")
def index_allocation_once_its_share_exists(sender, allocation, **kwargs):
    """Index a storage allocation in Starfish, and tag it, once the storage plugin has made its share.

    Not when the allocation is activated: that's when the share task starts, so its directory
    wasn't there yet for Starfish to scan. The storage plugin sends this from the worker that ran
    the share task, once the share exists.
    """
    if not SF_OWNER_TAG_PERSIST:
        logger.info(f"Not indexing allocation {allocation.pk} in Starfish: SF_OWNER_TAG_PERSIST is off.")
        return
    logger.debug(f"Allocation {allocation.pk}'s share exists. Queuing its Starfish indexing and tagging.")
    async_task(
        index_new_allocation, allocation_id=allocation.pk, q_options=index_task_options(allocation_id=allocation.pk)
    )


def is_starfish_approver(project_user) -> bool:
    """Whether the project's directories should carry an Approver tag for this member: an active
    Manager who isn't the PI, as update_sf_approver_tags writes them."""
    return (
        project_user.role.name == "Manager"
        and project_user.status.name == "Active"
        and project_user.user_id != project_user.project.pi_id
    )


@receiver(pre_save, sender=ProjectUser, dispatch_uid="tufts_local.note_approver_before_save")
def note_approver_before_save(sender, instance, raw=False, **kwargs):
    """Note whether the member was an approver before this save, for queue_approver_tags to compare."""
    before = None if raw or instance._state.adding else ProjectUser.objects.filter(pk=instance.pk).first()
    instance._was_starfish_approver = before is not None and is_starfish_approver(before)


@receiver(post_save, sender=ProjectUser, dispatch_uid="tufts_local.queue_approver_tags")
def queue_approver_tags(sender, instance, created, raw=False, **kwargs):
    """Queue the update of the project's Starfish Approver tags when this save changed its approvers.

    Whatever made the change: the approver toggle, Add Users, ColdFront's project-user page or the
    admin. Not for saves made by the nightly read-back of the tags, which come from the tags already.
    Queued once the save is committed, so the task reads the new roles.
    """
    if raw or not SF_OWNER_TAG_PERSIST or writing_roles_from_starfish():
        return
    if is_starfish_approver(instance) == getattr(instance, "_was_starfish_approver", False):
        return
    project_id = instance.project_id
    transaction.on_commit(lambda: _queue_approver_tags(project_id))


def _queue_approver_tags(project_id):
    # if even queueing fails (the broker is down, say), the role change still stands
    try:
        async_task(
            update_sf_approver_tags,
            project_id,
            q_options={"task_name": f"sf_approver_tags_{project_id}", "group": "starfish"},
        )
    except Exception:
        logger.exception(f"Could not queue the Starfish Approver tag update for project {project_id}")


class ActiveAllocationDeletion(PermissionDenied):
    """An Active allocation can't be deleted: revoke it first, so its users are taken off it.

    A PermissionDenied, not a plain Exception, so the admin answers a refused delete with a 403
    page rather than a server error.
    """


@receiver(pre_delete, sender=Allocation, dispatch_uid="tufts_local.record_resources_on_delete")
def record_resources_on_delete(sender, instance, **kwargs):
    """Refuse to delete an Active allocation; otherwise record its resources in the history row the delete writes.

    The allocation's history doesn't track its resources, so without this, once it's deleted
    nothing says what it was an allocation of. simple_history takes the deletion row's change reason
    from the instance, and Django sends pre_delete and post_delete with the same instance, while the
    resources can still be read. Raising here rolls back the whole delete, cascades included.
    """
    if instance.status.name == "Active":
        raise ActiveAllocationDeletion(f"Allocation {instance.pk} is Active: revoke it first, then delete it.")
    names = ", ".join(instance.resources.order_by("name").values_list("name", flat=True)) or "none"
    reason = f"Deleted; resources: {names}"
    # the history's change reason field holds 100 characters
    instance._change_reason = reason if len(reason) <= 100 else reason[:99] + "…"
