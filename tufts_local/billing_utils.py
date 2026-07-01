from logging import getLogger
from coldfront.core.allocation.models import Allocation
from coldfront.core.resource.models import Resource
from coldfront_billing.models import NoCostQuotaAllotment
from coldfront_utils import ttl_cache

logger = getLogger(__name__)

@ttl_cache(timeout=60*60)
def no_cost_quotas_report(user=None):
    """
    get info about no cost quotas for a given allocation.
    """
    data = []
    errors = []
    requires_payment = Resource.objects.filter(requires_payment=True, resource_type__name='Storage')
    shared_allotments = []
    if user:
        # only show allocations owned by the user (user is the PI of the project)
        allocations = Allocation.objects.filter(resources__in=requires_payment, status__name='Active', project__pi__username=user).order_by('project__pi__username', 'project__title').prefetch_related('allocationattribute_set')
    else:
        allocations = Allocation.objects.filter(resources__in=requires_payment, status__name='Active').order_by('project__pi__username', 'project__title').prefetch_related('allocationattribute_set')

    for allocation in allocations:
        try:
            vol_path = allocation.allocationattribute_set.filter(allocation_attribute_type__name='sf_vol_path').first().value
            quota = allocation.allocationattribute_set.filter(allocation_attribute_type__name='reported_usage_bytes').first().value
        except AttributeError:
            errors.append(f"Allocation {allocation.id} does not have required attributes.")
            continue
        storage_owner = allocation.project.pi.username
        info = {'allocation': allocation, 'vol_path': vol_path, 'storage_owner': storage_owner, 'quota': f"{int(quota)/10**12:.5f}", 'allotments': []}
        ncq_allotment = NoCostQuotaAllotment.objects.filter(allocation=allocation)
        ncq_allot_total = 0
        for allot in ncq_allotment:
            amount = float(allot.amount)
            ncq_allot_total += amount
            ncq = allot.no_cost_quota
            quota_type = ncq.quota_type
            #units = ncq.units
            #min_increment = ncq.minimum_increment
            ncq_owner = ncq.user.username
            info['allotments'].append({'amount': amount, 'quota_type': quota_type, 'ncq_owner': ncq_owner})
        info['ncq_allot_total'] = ncq_allot_total
        info['billable_quota'] = f"{max(0, int(quota)/10**12 - ncq_allot_total):.5f}"
        data.append(info)
    if user:
        # also look for any NoCostQuotaAllotments that the user owns, but are not associated with an allocation that they own
        ncq_allotments = NoCostQuotaAllotment.objects.filter(no_cost_quota__user__username=user).exclude(allocation__project__pi__username=user)
        if ncq_allotments.exists():
            for allot in ncq_allotments:
                allocation = allot.allocation
                vol_path = allocation.allocationattribute_set.filter(allocation_attribute_type__name='sf_vol_path').first().value
                quota = allocation.allocationattribute_set.filter(allocation_attribute_type__name='reported_usage_bytes').first().value
                storage_owner = allocation.project.pi.username
                amount = float(allot.amount)
                ncq_owner = allot.no_cost_quota.user.username
                quota_type = allot.no_cost_quota.quota_type
                shared_allotments.append({'allocation': allocation, 'vol_path': vol_path, 'storage_owner': storage_owner, 'quota': f"{int(quota)/10**12:.5f}", 'amount': amount, 'ncq_owner': ncq_owner, 'quota_type': quota_type})
    return {"allocations": data, "shared_allotments": shared_allotments, "errors": errors}