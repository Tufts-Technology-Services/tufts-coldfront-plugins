from logging import getLogger
from coldfront.core.allocation.models import Allocation
from coldfront.core.resource.models import Resource
from coldfront_billing.models import NoCostQuotaAllotment

logger = getLogger(__name__)

def no_cost_quotas_report():
    """
    get info about no cost quotas for a given allocation.
    """
    data = []
    errors = []
    requires_payment = Resource.objects.filter(requires_payment=True, resource_type__name='Storage')
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
    return {"allocations": data, "errors": errors}