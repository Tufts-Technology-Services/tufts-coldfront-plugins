from logging import getLogger
from coldfront.core.allocation.models import Allocation
from coldfront.core.resource.models import Resource
from coldfront.core.project.models import Project
from coldfront_billing.models import NoCostQuotaAllotment, CostCenterAssignment
from django.core.exceptions import ObjectDoesNotExist
from django.utils.timezone import localdate
from coldfront_billing.constants import (
    BILLING_ATTRIBUTE_NAME,
)
from coldfront_billing.data.billing import create_billing_allocations
from coldfront_billing.prefetch import get_projects_prefetch
from coldfront_billing.utils import get_month_abbr
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


def billing_code_audit(user=None):
    """
    get info about billing codes for all allocations requiring payment.
    """
    missing_billing_code = []
    month = localdate().strftime("%Y-%m")
    if user:
        projects = get_projects_prefetch(Project.objects.filter(pi__username=user))
    else:   
        projects = get_projects_prefetch(Project.objects.all().order_by('pi__username', 'title'))
    total_cost = 0
    charge_report = []
    for project in projects:
        billing_allocations = create_billing_allocations(getattr(project, BILLING_ATTRIBUTE_NAME, []))
        project_cost = sum(b.total_cost for b in billing_allocations)

        if not project_cost:
            continue
        total_cost += project_cost
        try:
            assignments = project.cost_center_assignment.assignments or []
        except ObjectDoesNotExist:
            # if the project does not have a CostCenterAssignment, we need to report these projects as missing billing codes
            missing_billing_code.append({'project': project, 'project_cost': project_cost})
            continue

        for assignment in sorted(assignments, key=lambda x: x.get('department')):
            dept_id = assignment.get('department')
            pct = assignment.get('percentage')
            grant = assignment.get('grant', '')
            cost = round(project_cost * (int(pct) / 100), 2)
            charges = {
                'project': project,
                'department': dept_id,
                'grant': grant,
                'percentage': pct,
                'charged_to_id': cost,
                'project_cost': project_cost,
            }
            charge_report.append(charges)

    return {"missing_billing_code": missing_billing_code, "charge_report": charge_report, "total_cost": round(total_cost, 2), "month": month }
