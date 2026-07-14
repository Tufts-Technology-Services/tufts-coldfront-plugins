import logging
from django.dispatch import receiver
from django_q.tasks import async_task
from coldfront.core.allocation.models import Allocation
from coldfront.core.allocation.signals import allocation_activate
from .constants import SF_OWNER_TAG_PERSIST
from .tasks import set_sf_owner_tag

logger = logging.getLogger(__name__)


@receiver(allocation_activate)
def handle_allocation_activate(sender, **kwargs):
    """
    When an allocation is activated, we want to update the owner tags in Starfish for the associated project.
    """
    allocation_id = kwargs.get('allocation_pk')
    allocation = Allocation.objects.get(id=allocation_id)
    if allocation.status.name not in ['Active']:
        logger.debug(f"Allocation {allocation_id} is not active. Skipping Starfish 'Owner' tagging.")
        return
    if SF_OWNER_TAG_PERSIST:
        logger.debug(f"Allocation {allocation.id} activated. Scheduling task to update owner tags in Starfish.")
        async_task(set_sf_owner_tag, allocation_id)
    else:
        logger.info(f"Allocation {allocation.id} activated. Skipping task to update owner tags in Starfish because SF_OWNER_TAG_PERSIST is False.")








