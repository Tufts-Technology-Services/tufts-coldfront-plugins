from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal
import logging
from django.db.models import TextField
from django.db.models.functions import Cast
from django.core.exceptions import ObjectDoesNotExist
from django.utils.timezone import localdate
from coldfront.core.allocation.models import Allocation, AllocationAttribute
from coldfront.core.resource.models import Resource
from coldfront.core.project.models import Project
from coldfront_billing.models import NoCostQuotaAllotment, CostCenterAssignment

from coldfront_billing.constants import (
    BILLING_ATTRIBUTE_NAME,
)
from coldfront_billing.data.billing import create_billing_allocations
from coldfront_billing.prefetch import get_projects_prefetch
from coldfront_utils import ttl_cache

logger = logging.getLogger(__name__)

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
        allocations = Allocation.objects.filter(resources__in=requires_payment, status__name='Active', project__pi__username=user).order_by('project__pi__username', 'project__title').prefetch_related('allocationattribute_set', 'no_cost_quota_allotments')
    else:
        allocations = Allocation.objects.filter(resources__in=requires_payment, status__name='Active').order_by('project__pi__username', 'project__title').prefetch_related('allocationattribute_set', 'no_cost_quota_allotments')

    for allocation in allocations:
        try:
            vol_path = allocation.allocationattribute_set.filter(allocation_attribute_type__name='sf_vol_path').first().value
            quota = allocation.allocationattribute_set.filter(allocation_attribute_type__name='reported_usage_bytes').first().value
        except AttributeError:
            errors.append(f"Allocation {allocation.id} does not have required attributes.")
            continue
        storage_owner = allocation.project.pi.username
        info = {'allocation': allocation, 'vol_path': vol_path, 'storage_owner': storage_owner, 'quota': f"{int(quota)/10**12:.5f}", 'allotments': []}
        ncq_allotment = allocation.no_cost_quota_allotments.all()
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


def billing_code_report(user=None):
    """
    get info about billing codes for all allocations requiring payment.
    """

    month = localdate().strftime("%Y-%m")
    if user:
        projects = get_projects_prefetch(Project.objects.filter(pi__username=user))
    else:   
        projects = get_projects_prefetch(Project.objects.all().order_by('pi__username', 'title'))
    total_cost = 0
    missing_billing_code = []
    for project in projects:
        billing_allocations = create_billing_allocations(getattr(project, BILLING_ATTRIBUTE_NAME, []))
        project_cost = sum(b.total_cost for b in billing_allocations)

        if project_cost:

            try:
                _ = project.cost_center_assignment.assignments or []
            except ObjectDoesNotExist:
                total_cost += project_cost
                # if the project does not have a CostCenterAssignment, we need to report these projects as missing billing codes
                missing_billing_code.append({'project': project, 'project_cost': project_cost})

    return {"missing_billing_code": missing_billing_code, "total_cost": round(total_cost, 2), "month": month }


def get_cost_previews(user=None, billing_code=None):
    """
    get info about costs based on current billing allocations for all projects with a given billing code and/or owned by a given user.
    """
    month = localdate().strftime("%Y-%m")
    missing_billing_code = []
    if billing_code and user:
        c = CostCenterAssignment.objects.filter(project__pi__username=user).annotate(json_as_text=Cast('assignments', TextField())).filter(json_as_text__icontains=f"\"{billing_code}")
        projects = get_projects_prefetch(Project.objects.filter(cost_center_assignment__in=c).order_by('pi__username', 'title'))
    elif billing_code:
        c = CostCenterAssignment.objects.annotate(json_as_text=Cast('assignments', TextField())).filter(json_as_text__icontains=f"\"{billing_code}")
        projects = get_projects_prefetch(Project.objects.filter(cost_center_assignment__in=c).order_by('pi__username', 'title'))
    elif user:
        projects = get_projects_prefetch(Project.objects.filter(pi__username=user).order_by('pi__username', 'title'))
    else:   
        projects = get_projects_prefetch(Project.objects.all().order_by('pi__username', 'title'))
    total_cost = 0
    charge_report = []
    for project in projects:
        billing_allocations = create_billing_allocations(getattr(project, BILLING_ATTRIBUTE_NAME, []))
        project_cost = sum(b.total_cost for b in billing_allocations)
        for b in billing_allocations:
            b.ncq_applied = sum([i.normalized_amount for i in b.no_cost_quotas])
                
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
                'allocations': billing_allocations,
                'department': dept_id,
                'grant': grant,
                'percentage': pct,
                'charged_to_id': cost,
                'project_cost': project_cost,
            }
            charge_report.append(charges)

    return {"missing_billing_code": missing_billing_code, "charge_report": charge_report, "total_cost": round(total_cost, 2), "month": month }


def get_cost_per_allocation():
    rows = []
    projects = get_projects_prefetch(Project.objects.all().order_by('pi__username', 'title'))
    for project in projects:
        billing_allocations = create_billing_allocations(getattr(project, BILLING_ATTRIBUTE_NAME, []))
        for ba in billing_allocations:
            vol_path = AllocationAttribute.objects.filter(allocation_id=ba.allocation_id, allocation_attribute_type__name="sf_vol_path").first().value
            rows.append({
                'owner': ba.extra.get('pi_name'),
                'resource': ba.extra.get('resource_name'),
                'vol_path': vol_path,
                'quota_tb': ba.quota_tb,
                'cost_per_tb': ba.cost_per_tb,
                'cost': (Decimal(ba.quota_tb).quantize(Decimal('0.1'), rounding=ROUND_CEILING) * Decimal(ba.cost_per_tb).quantize(Decimal('0.01'), rounding=ROUND_CEILING)).quantize(Decimal('0.01'), rounding=ROUND_CEILING),
                'ncq_applied': sum([i.amount for i in ba.no_cost_quotas]),
                'cost_after_ncq': ba.total_cost
            })
    return rows


def get_oversubscribed_no_cost_quotas():
    """
    return any NoCostQuotaAllotments that are associated with an allocation where allotment total exceeds allocation quota.
    # todo: check the rounding on this
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


def expired_storage_allocations_with_ncq_allotments():
    """
    get info about storage allocations that have expired and require payment, including the vol_path and the storage owner.
    """
    storage = Resource.objects.filter(resource_type__name='Storage')
    expired_allocations = Allocation.objects.filter(resources__in=storage).exclude(status__name="Active").prefetch_related('allocationattribute_set', 'project__pi')
    data = []
    for allocation in expired_allocations:
        ncq_allotment = NoCostQuotaAllotment.objects.filter(allocation=allocation)
        if ncq_allotment.exists():
            vol_path = allocation.allocationattribute_set.filter(allocation_attribute_type__name='sf_vol_path').first().value
            storage_owner = allocation.project.pi.username
            resource = allocation.resources
            resource_name = resource.first().name if resource.exists() else ''
            requires_payment = resource.first().requires_payment if resource.exists() else False
            status = allocation.status.name
            
            data.append({'owner': storage_owner, 'resource': resource_name, 'vol_path': vol_path, 'requires_payment': requires_payment, 'status': status,  'allocation': allocation, 'ncq_allotments': ncq_allotment})
    return data


def get_all_storage_allocations():
    """
    get info about storage allocations that have expired and require payment, including the vol_path and the storage owner.
    """
    storage = Resource.objects.filter(resource_type__name='Storage')
    allocations = Allocation.objects.filter(resources__in=storage).prefetch_related('allocationattribute_set', 'project__pi', 'no_cost_quota_allotments')
    data = []
    for allocation in allocations:
        vol_path_attr = allocation.allocationattribute_set.filter(allocation_attribute_type__name='sf_vol_path')
        vol_path = vol_path_attr.first().value if vol_path_attr.exists() else 'NA'
        quota_attr = allocation.allocationattribute_set.filter(allocation_attribute_type__name='reported_quota_bytes')
        if quota_attr.exists():
            quota = quota_attr.first().value
            quota = (Decimal(quota)/10**12).quantize(Decimal('0.01'), rounding=ROUND_CEILING)
        else:
            quota = 'NA'
        usage = allocation.allocationattribute_set.filter(allocation_attribute_type__name='reported_usage_bytes')
        if usage.exists():
            usage = usage.first().value
            usage = (Decimal(usage)/10**12).quantize(Decimal('0.0001'), rounding=ROUND_CEILING)
        else:
            usage = 'NA'
        storage_owner = allocation.project.pi.username
        resource = allocation.resources
        resource_name = resource.first().name if resource.exists() else ''
        requires_payment = resource.first().requires_payment if resource.exists() else False
        status = allocation.status.name
        ncq_allotment = allocation.no_cost_quota_allotments.all()
        if ncq_allotment.exists():
            for allot in ncq_allotment:
                data.append({'owner': storage_owner, 'resource': resource_name, 
                             'vol_path': vol_path, 'requires_payment': requires_payment, 
                             'status': status,  'quota': quota, 'usage': usage,
                             'ncq_owner': allot.no_cost_quota.user, 'ncq_amount': allot.amount, 'ncq_quota_type': allot.no_cost_quota.quota_type})
        else:
            data.append({'owner': storage_owner, 'resource': resource_name, 
                         'vol_path': vol_path, 'requires_payment': requires_payment, 
                         'status': status,  'quota': quota, 'usage': usage,
                         'ncq_owner': None, 'ncq_amount': None, 'ncq_quota_type': None})
    return data
