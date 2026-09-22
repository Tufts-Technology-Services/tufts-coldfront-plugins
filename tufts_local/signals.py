# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging

from django.dispatch import receiver
from django_q.tasks import async_task

from coldfront.core.allocation.models import Allocation
from coldfront.core.allocation.signals import allocation_activate

from .constants import SF_OWNER_TAG_PERSIST
from .tasks import index_new_allocation

logger = logging.getLogger(__name__)


@receiver(allocation_activate)
def handle_allocation_activate(sender, **kwargs):
    """
    When an allocation is activated, we want to update the owner tags in Starfish for the associated project.
    """
    allocation_id = kwargs.get('allocation_pk')
    allocation = Allocation.objects.get(id=allocation_id)
    if allocation.status.name not in ['Active']:
        logger.debug(f'Allocation {allocation_id} is not active. Skipping Starfish initialization.')
        return
    if SF_OWNER_TAG_PERSIST:
        logger.debug(f'Allocation {allocation.id} activated. Scheduling task to update owner tags in Starfish.')
        options = {
            'task_name': f'add_sf_tags_alloc_activate_{allocation_id}',
            'group': 'starfish',
        }
        async_task(index_new_allocation, allocation_id, q_options=options)
    else:
        logger.info(
            f'Allocation {allocation.id} activated. Skipping task to update owner tags in Starfish because SF_OWNER_TAG_PERSIST is False.'
        )
