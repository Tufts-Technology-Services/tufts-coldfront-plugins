import logging
import csv
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.http import require_GET
from django.http import JsonResponse, HttpResponse
from django.template.response import TemplateResponse
from django.views.decorators.cache import cache_page
from coldfront.core.allocation.models import Allocation, AllocationAttribute
from coldfront.core.resource.models import Resource
from tufts_local import billing_utils
from tufts_local import utils
from tufts_local.forms import ReportFilterForm
from tufts_local.starfish_utils import get_starfish_usage_data_by_volume, get_starfish_volumes, parse_tags

logger = logging.getLogger(__name__)


@login_required
@require_GET
@user_passes_test(lambda u: u.is_superuser)
@cache_page(60 * 5)  # Cache the view for 5 minutes
def sf_report(request):
    volumes = get_starfish_volumes("starfish")
    sf_data = []
    for volume in volumes:
        sf_data.extend(get_starfish_usage_data_by_volume(volume, "starfish"))
    vol_paths = set([i['vol_path'].lower().strip() for i in sf_data])
    storage = Resource.objects.filter(resource_type__name='Storage')
    storage_allocations = Allocation.objects.filter(resources__in=storage).prefetch_related('allocationattribute_set')
    missing_sf_attribute = []
    not_in_starfish = []
    alloc_matches = {}
    for alloc in storage_allocations:
        alloc_sf_attr = alloc.allocationattribute_set.filter(allocation_attribute_type__name="sf_vol_path")
        sf_vol_path = alloc_sf_attr.first().value if alloc_sf_attr.exists() else None
        if not sf_vol_path:
            missing_sf_attribute.append(alloc)
        elif sf_vol_path.lower().strip() not in vol_paths:
            # only consider active allocations for not_in_starfish, as there could be archived allocations that are not in Starfish anymore
            if alloc.status.name.lower() == "active":
                not_in_starfish.append(alloc)
        else:
            alloc_matches[sf_vol_path] = alloc
    
    owner_mismatches = []
    approver_mismatches = []
    for k, v in alloc_matches.items():
        sf_entry = next((item for item in sf_data if item['vol_path'].lower().strip() == k.lower().strip()), None)
        if sf_entry:
            tags = parse_tags(sf_entry.get('tags_explicit', '').split(','))
            if tags:
                sf_owner = tags.get('Owner', set())
                if len(sf_owner) > 0:
                    sf_owner = list(sf_owner)[0]
                    if v.project and v.project.pi.username != sf_owner:
                        owner_mismatches.append((k, v, v.project.pi.username, sf_owner))
                sf_approvers = tags.get('Approver', set())
                sf_approvers.discard(v.project.pi.username)  # remove the owner from the approvers list if present  
                if v.project:
                    cf_approvers = set([pu.user.username for pu in v.project.projectuser_set.filter(role__name="Manager", status__name="Active")])
                    cf_approvers.discard(v.project.pi.username)
                    if cf_approvers != sf_approvers:
                        approver_mismatches.append((k, v, cf_approvers, sf_approvers))
    
    vol_path_allocation_attributes = set(list(AllocationAttribute.objects.filter(
        allocation__in=storage_allocations, 
        allocation_attribute_type__name="sf_vol_path").values_list('value', flat=True)))
    vol_path_allocation_attributes = {i.lower().strip() for i in vol_path_allocation_attributes}
    missing_from_coldfront = vol_paths - vol_path_allocation_attributes
    if request.GET.get("format") == "csv":
        data = {
            "header": ["sf_status", "sf_vol_path", "Resource Allocation", "Project", "Status"],
            "rows": []
        }
        for allocation in missing_sf_attribute:
            sf_vol_path = allocation.allocationattribute_set.filter(allocation_attribute_type__name="sf_vol_path").first()
            sf_vol_path_value = sf_vol_path.value if sf_vol_path else ""
            data["rows"].append([
                "Missing sf_vol_path attribute",
                sf_vol_path_value,
                allocation.get_parent_resource.name,
                allocation.project.title if allocation.project else "",
                allocation.status.name
            ])
        for allocation in not_in_starfish:
            sf_vol_path = allocation.allocationattribute_set.filter(allocation_attribute_type__name="sf_vol_path").first()
            sf_vol_path_value = sf_vol_path.value if sf_vol_path else ""
            data["rows"].append([
                "sf_vol_path not in Starfish",
                sf_vol_path_value,
                allocation.get_parent_resource.name,
                allocation.project.title if allocation.project else "",
                allocation.status.name
            ])
        for vol_path in missing_from_coldfront:
            data["rows"].append([
                "In Starfish but not in Coldfront",
                vol_path,
                "",
                "",
                ""
            ])
        for vol_path, allocation, sf_owner in owner_mismatches:
            data["rows"].append([
                "Owner mismatch",
                vol_path,
                allocation.get_parent_resource.name,
                allocation.project.title if allocation.project else "",
                f"Coldfront Owner: {allocation.project.pi.username}, Starfish Owner: {sf_owner}"
            ])
        for vol_path, allocation, cf_approvers, sf_approvers in approver_mismatches:
            data["rows"].append([
                "Approver mismatch",
                vol_path,
                allocation.get_parent_resource.name,
                allocation.project.title if allocation.project else "",
                f"Coldfront Approvers: {', '.join(cf_approvers)}, Starfish Approvers: {', '.join(sf_approvers)}"
            ])
        return get_csv(data, filename="starfish_diff_report.csv")
    return TemplateResponse(request, "tufts_local/sf_report.html", {"volumes": volumes,
                                                                    "missing_starfish_attribute": missing_sf_attribute,
                                                                    "not_in_starfish": not_in_starfish,
                                                                    "missing_from_coldfront": missing_from_coldfront,
                                                                    "owner_mismatches": owner_mismatches,
                                                                    "approver_mismatches": approver_mismatches
                                                                    })

def get_csv(data, filename="export.csv"):
    response = HttpResponse(
        content_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
    writer = csv.writer(response)
    # Write CSV header
    writer.writerow(data['header'])
    # Write CSV data row
    for row in data['rows']:
        writer.writerow(row)
    return response



@login_required
@require_GET
def no_cost_quotas_report(request):
    if request.user.is_superuser:
        format = request.GET.get("format")
        form = ReportFilterForm(request.GET)
        if form.is_valid() and format != "json":
            if form.cleaned_data['username']:
                data = billing_utils.no_cost_quotas_report(user=form.cleaned_data['username'])
            else:
                data = billing_utils.no_cost_quotas_report()
        else:
            data = billing_utils.no_cost_quotas_report()
            
        if format == "json":
            return JsonResponse(data, status=200)
        else:
           return TemplateResponse(request, "tufts_local/no_cost_quotas_report.html", {"message": data['errors'], "allocations": data['allocations'], "shared_allotments": data['shared_allotments'], "form": form})
    else:
        data = billing_utils.no_cost_quotas_report(user=request.user.username)
        return TemplateResponse(request, "tufts_local/no_cost_quotas_report.html", {"message": data['errors'], "allocations": data['allocations'], "shared_allotments": data['shared_allotments']})


@login_required
@require_GET
def billing_code_report(request):
    if request.user.is_superuser:
        form = ReportFilterForm(request.GET)
        if form.is_valid():
            if form.cleaned_data['username']:
                data = billing_utils.billing_code_report(user=form.cleaned_data['username'])
            else:
                data = billing_utils.billing_code_report()

            if request.GET.get("format") == "json":
                return JsonResponse(data, status=200)
            else:
               return TemplateResponse(request, "tufts_local/billing_code_report.html", {"missing_billing_code": data['missing_billing_code'], "total_cost": data['total_cost'], "month": data['month'], "form": form})
        else:
            return TemplateResponse(request, "tufts_local/billing_code_report.html", {"message": "Invalid form data."}, status=400)
    else:
        data = billing_utils.billing_code_report(user=request.user.username)
        return TemplateResponse(request, "tufts_local/billing_code_report.html", {"missing_billing_code": data['missing_billing_code'], "total_cost": data['total_cost'], "month": data['month']})


@login_required
@require_GET
def charge_report(request):
    if request.user.is_superuser:
        form = ReportFilterForm(request.GET)
        if form.is_valid():
            username = form.cleaned_data['username']
            billing_code = form.cleaned_data['billing_code']
            data = billing_utils.get_cost_previews(user=username, billing_code=billing_code)

            if request.GET.get("format") == "json":
                return JsonResponse(data, status=200)
            else:
               return TemplateResponse(request, "tufts_local/charge_report.html", {"charge_report": data['charge_report'], "total_cost": data['total_cost'], "month": data['month'], "form": form})
        else:
            return TemplateResponse(request, "tufts_local/charge_report.html", {"message": "Invalid form data."}, status=400)
    else:
        if form := ReportFilterForm(request.GET):
            if form.is_valid():
                billing_code = form.cleaned_data['billing_code']
                data = billing_utils.get_cost_previews(user=request.user.username, billing_code=billing_code)
                return TemplateResponse(request, "tufts_local/charge_report.html", {"charge_report": data['charge_report'], "total_cost": data['total_cost'], "month": data['month'], "form": form})


@user_passes_test(lambda u: u.is_superuser)
@require_GET
def not_updated_report(request):
    data = utils.not_updated_report()
    return TemplateResponse(request, "tufts_local/not_updated_report.html", data)


@user_passes_test(lambda u: u.is_superuser)
@require_GET
def oversubscribed_allotments_report(request):
    data = billing_utils.get_oversubscribed_no_cost_quotas()
    return TemplateResponse(request, "tufts_local/oversubscribed_allotments_report.html", {'overages': data})


@user_passes_test(lambda u: u.is_superuser)
@require_GET
def expired_allocations_report(request):
    data = billing_utils.expired_storage_allocations_with_ncq_allotments()
    return TemplateResponse(request, "tufts_local/expired_allocations_report.html", {'expired_allocations': data})
