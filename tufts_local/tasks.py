import logging

from decimal import Decimal, ROUND_CEILING, ROUND_HALF_EVEN
from django.contrib.auth.models import Group, User
from coldfront.core.project.models import Project, ProjectUser
from coldfront.core.allocation.models import Allocation, AllocationAttribute
from coldfront.core.resource.models import Resource
from coldfront_billing.models import NoCostQuotaAllotment, NoCostQuota
from coldfront_billing.views.no_cost_quota.common import quota_with_remaining
from coldfront_billing.reports.quota import auto_assign_quota 
from tufts_local.analytics_utils import get_ncq_eligibility
from tufts_local.billing_utils import no_cost_quotas_report
from tufts_local.starfish_utils import (get_starfish_usage_data_by_volume, get_starfish_volumes,
                                        set_project_approvers_from_starfish,
                                        sync_approver_tags, 
                                        set_owner_tag)
from tufts_local.utils import create_user, setup_custom_logger

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


def send_ncq_report():
    report = no_cost_quotas_report()
    for i in report['allocations']:
        del i['allocation']  # remove allocation object from report to avoid serialization issues
    
    ncq_remaining_report = []
    for i in quota_with_remaining():
        ncq_remaining_report.append({
            'user': i.user.username,
            'quota_type': i.quota_type,
            'amount': i.amount,
            'remaining': round(i.remaining, 2)
        })
    return ncq_remaining_report


def refresh_ncq_eligibility():
    ncq_logger = setup_custom_logger('ncq', 'ncq.log')
    eligibility_data = get_ncq_eligibility()
    tier1_group_name = 'faculty_pi_eligible_tier1'
    tier2_group_name = 'faculty_pi_eligible_tier2'
    group_tier1 = Group.objects.get(name=tier1_group_name)
    group_tier2 = Group.objects.get(name=tier2_group_name)
    tier1_users = User.objects.filter(groups=group_tier1).values_list('username', flat=True)
    tier2_users = User.objects.filter(groups=group_tier2).values_list('username', flat=True)
    for entry in eligibility_data:
        is_eligible = entry.get('no_cost_quota_eligible', 'No')
        username = entry.get('username', '').lower()
        in_tier_1 = username in tier1_users
        in_tier_2 = username in tier2_users
        if is_eligible.lower().strip() == 'yes':
            if not in_tier_1:
                ncq_logger.info(f"Adding user {username} to group {tier1_group_name}")
                user = create_user(username)
                user.groups.add(group_tier1)
            if not in_tier_2:
                ncq_logger.info(f"Adding user {username} to group {tier2_group_name}")
                user = create_user(username)
                user.groups.add(group_tier2)
        else:
            if in_tier_1:
                ncq_logger.info(f"Removing user {username} from group {tier1_group_name}")
                user = create_user(username)
                user.groups.remove(group_tier1)
                allotments = NoCostQuotaAllotment.objects.filter(allocation__project__pi=user)
                if allotments.exists():
                    ncq_logger.info(f"User {username} is no longer eligible for NoCostQuota. Deleting {allotments.count()} associated NoCostQuotaAllotment(s).")
                    for allotment in allotments:
                        ncq_logger.info(f"Deleting NoCostQuotaAllotment {allotment.amount} associated with allocation {allotment.allocation.id}.")
                        allotment.delete()
                NoCostQuota.objects.filter(user=user).delete()
            if in_tier_2:
                ncq_logger.info(f"Removing user {username} from group {tier2_group_name}")
                user = create_user(username)
                user.groups.remove(group_tier2)
                allotments = NoCostQuotaAllotment.objects.filter(allocation__project__pi=user)
                if allotments.exists():
                    ncq_logger.info(f"User {username} is no longer eligible for NoCostQuota. Deleting {allotments.count()} associated NoCostQuotaAllotment(s).")
                    for allotment in allotments:
                        ncq_logger.info(f"Deleting NoCostQuotaAllotment {allotment.amount} associated with allocation {allotment.allocation.id}.")
                        allotment.delete()
                NoCostQuota.objects.filter(user=user).delete()


def remove_empty_ncq_allotments():
    empty_allotments = NoCostQuotaAllotment.objects.filter(amount__lte=0)
    count = empty_allotments.count()
    if count > 0:
        logger.info(f"Removing {count} NoCostQuotaAllotment(s) with amount 0.")
        empty_allotments.delete()
    return f"Removed {count} NoCostQuotaAllotment(s) with amount 0."


def autoallocate_ncq_allotments():
    """
    Automatically allocate NoCostQuotaAllotments to eligible users based on their remaining quota.
    """
    logger.info("Starting automatic allocation of NoCostQuotaAllotments.")
    response = auto_assign_quota()
    logger.info(f"Automatic allocation response: {response}")
    return response
