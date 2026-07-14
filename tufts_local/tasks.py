from decimal import Decimal, ROUND_CEILING, ROUND_HALF_EVEN
from coldfront.core.project.models import Project, ProjectUser
from coldfront.core.allocation.models import Allocation, AllocationAttribute
from coldfront.core.resource.models import Resource
from coldfront_billing.models import NoCostQuotaAllotment

from tufts_local.starfish_utils import get_starfish_usage_data_by_volume, sync_approver_tags
from tufts_local.utils import update_project_approvers_from_subfolder_tags


def update_project_approvers_from_tags():
    for volume in ['projects', 'tier2', 'cold', 'cold2', 'rstore-cifs', 'rstore-nfs']:
        subfolder_response = get_starfish_usage_data_by_volume(volume, 'starfish')
        update_project_approvers_from_subfolder_tags(subfolder_response)


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
