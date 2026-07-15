import logging

from decimal import Decimal, ROUND_CEILING, ROUND_HALF_EVEN
from coldfront.core.project.models import Project, ProjectUser
from coldfront.core.allocation.models import Allocation, AllocationAttribute
from coldfront.core.resource.models import Resource
from coldfront_billing.models import NoCostQuotaAllotment

from tufts_local.starfish_utils import (get_starfish_usage_data_by_volume, get_starfish_volumes,
                                        set_project_approvers_from_starfish,
                                        sync_approver_tags, 
                                        set_owner_tag)

logger = logging.getLogger(__name__)


def update_project_approvers_from_tags():
    for volume in get_starfish_volumes('starfish'):
        subfolder_response = get_starfish_usage_data_by_volume(volume, 'starfish')
        for vol_path_data in subfolder_response:
            set_project_approvers_from_starfish(vol_path_data)


def update_sf_approver_tags(project_id):
    # write starfish tags corresponding to project approvers for all projects in Coldfront
    proj = Project.objects.get(id=project_id)
    approvers = ProjectUser.objects.filter(project=proj, role__name='Manager')
    approver_usernames = []
    if approvers.exists():
        approver_usernames = [a.user.username for a in approvers]
    alloc_attr = AllocationAttribute.objects.filter(allocation__project=proj, allocation_attribute_type__name='sf_vol_path')
    for attr in alloc_attr:
        vol_path = attr.value
        sync_approver_tags(vol_path, approver_usernames, 'starfish')


def update_sf_owner_tags_from_allocations():
    # write starfish tag corresponding to project owner for all allocations in Coldfront
    storage = Resource.objects.filter(resource_type__name='Storage')
    allocations = Allocation.objects.filter(status__name='Active', resources__in=storage).prefetch_related('allocationattribute_set', 'project__pi')
    for alloc in allocations:
        _set_sf_owner_tag(alloc)


def set_sf_owner_tag(allocation_id):
    # write starfish tag corresponding to project owner for all allocations in Coldfront
    storage = Resource.objects.filter(resource_type__name='Storage')
    alloc_set = Allocation.objects.filter(id=allocation_id, resources__in=storage).prefetch_related('allocationattribute_set', 'project__pi')
    if not alloc_set.exists():
        logger.debug(f"Allocation {allocation_id} does not have a storage resource. Skipping Starfish 'Owner' tagging.")
        return
    alloc = alloc_set.first()
    _set_sf_owner_tag(alloc)


def _set_sf_owner_tag(allocation):
    owner = allocation.project.pi.username
    alloc_attr = allocation.allocationattribute_set.filter(allocation_attribute_type__name='sf_vol_path').first()
    if alloc_attr:
        vol_path = alloc_attr.value
        logger.debug(f"Setting Starfish 'Owner' tag for allocation {allocation.id} with vol_path {vol_path} to {owner}.")
        set_owner_tag('starfish', vol_path, owner)
    else:
        logger.error(f"Allocation {allocation.id} does not have an 'sf_vol_path' attribute. Cannot set Starfish 'Owner' tag.")


def get_oversubscribed_no_cost_quotas():
    """
    return any NoCostQuotaAllotments that are associated with an allocation where allotment total exceeds allocation quota.
    """
    requires_payment = Resource.objects.filter(requires_payment=True, resource_type__name='Storage')
    allocations = Allocation.objects.filter(resources__in=requires_payment, status__name='Active').prefetch_related('allocationattribute_set')
    exceeded_allotments = []
    for allocation in allocations:
        try:
            quota = allocation.allocationattribute_set.filter(allocation_attribute_type__name='reported_quota_bytes').first().value
        except AttributeError:
            print(f"Allocation {allocation.id} does not have required attributes.")
            continue
        ncq_allotment = NoCostQuotaAllotment.objects.filter(allocation=allocation)
        ncq_allot_total = sum(float(allot.amount) for allot in ncq_allotment)
        total_rounded = Decimal(ncq_allot_total).quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)
        quota_rounded = Decimal(int(quota)/10**12).quantize(Decimal('0.01'), rounding=ROUND_CEILING)
        if quota_rounded < total_rounded:
            exceeded_allotments.append({'allocation': allocation, 'quota': quota_rounded, 
                                        'total_allotments': total_rounded, 
                                        'ncq_allotments': ncq_allotment})
    return exceeded_allotments
