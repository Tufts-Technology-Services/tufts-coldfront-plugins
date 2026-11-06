# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging

from coldfront_utils.signals import share_creation_successful
from django.dispatch import receiver
from django_q.tasks import async_task

from .constants import SF_OWNER_TAG_PERSIST
from .tasks import index_new_allocation, index_task_options

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
