import csv
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.template.response import TemplateResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import cache_page
from django_q.tasks import async_task
from coldfront.core.project.models import ProjectUser, ProjectUserRoleChoice
from coldfront.core.allocation.models import Allocation, AllocationAttribute
from coldfront.core.resource.models import Resource
from tufts_local import billing_utils, utils
from tufts_local.forms import ReportFilterForm
from tufts_local.starfish_utils import get_starfish_usage_data_by_volume
from tufts_local.tasks import update_sf_approver_tags


@login_required
def project_update_user_role(request):
    if request.method == "POST":
        data = request.POST
        project_user_obj = get_object_or_404(ProjectUser, id=data.get("user_project_id"))

        project_obj = project_user_obj.project

        allowed = False
        if project_obj.pi == request.user:
            allowed = True

        if project_obj.projectuser_set.filter(user=request.user, role__name="Manager", status__name="Active").exists():
            allowed = True

        if project_user_obj.user == request.user:
            allowed = True

        if request.user.is_superuser:
            allowed = True

        if allowed is False:
            return JsonResponse({"message": "not allowed"}, status=403)
        else:
            role_name = data.get("role")
            if role_name:
                project_user_role = ProjectUserRoleChoice.objects.get(name=role_name)
                if project_user_role:
                    project_user_obj.role = project_user_role
                    project_user_obj.save()
                    # update the Starfish tags for the project approvers
                    r = async_task(update_sf_approver_tags, project_user_obj.project.id)
                    return JsonResponse({"message": "role updated", "task_id": r}, status=200)
                else:
                    return JsonResponse({"message": "invalid role specified"}, status=400)
            else:
                return JsonResponse({"message": "no role specified"}, status=400)
    else:
        return JsonResponse({"message": "no POST"}, status=400)


@login_required
def project_get_email_notification(request, project_user_id):
    if request.method == "GET":
        project_user_obj = get_object_or_404(ProjectUser, id=project_user_id)

        project_obj = project_user_obj.project

        allowed = False
        if project_obj.pi == request.user:
            allowed = True

        if project_obj.projectuser_set.filter(user=request.user, role__name="Manager", status__name="Active").exists():
            allowed = True

        if project_user_obj.user == request.user:
            allowed = True

        if request.user.is_superuser:
            allowed = True

        if allowed is False:
            return JsonResponse({"message": "not allowed"}, status=403)
        else:
            return JsonResponse({"enable_notifications": True}, status=200)
    else:
        return JsonResponse({"message": f"unsupported method {request.method}"}, status=400)


@login_required
def utln_autocomplete(request):
    if request.user.is_superuser is False:
        return JsonResponse({"message": "not allowed"}, status=403)
    if request.method == "GET":
        query = request.GET.get("query", "")
        # Placeholder for actual autocomplete logic, e.g., querying an external service
        suggestions = utils.user_autocomplete(query)  # Replace with actual suggestions based on the query
        return JsonResponse({"suggestions": suggestions}, status=200)
    else:
        return JsonResponse({"message": f"unsupported method {request.method}"}, status=400)


@login_required
@cache_page(60 * 5)  # Cache the view for 5 minutes
def sf_report(request):
    if request.user.is_superuser is False:
        return TemplateResponse("tufts_local/sf_report.html", {"message": "not allowed"}, status=403)
    if request.method != "GET":
        return TemplateResponse("tufts_local/sf_report.html", {"message": f"unsupported method {request.method}"}, status=400)
    volumes = utils.get_sf_volumes_in_coldfront()
    sf_data = []
    for volume in volumes:
        sf_data.extend(get_starfish_usage_data_by_volume(volume, "starfish"))
    sf_data = set([i['vol_path'].lower().strip() for i in sf_data])
    storage = Resource.objects.filter(resource_type__name='Storage')
    storage_allocations = Allocation.objects.filter(resources__in=storage).prefetch_related('allocationattribute_set')
    missing_sf_attribute = []
    not_in_starfish = []
    for alloc in storage_allocations:
        alloc_sf_attr = AllocationAttribute.objects.filter(allocation=alloc, allocation_attribute_type__name="sf_vol_path")
        if not alloc_sf_attr.exists():
            missing_sf_attribute.append(alloc)
        elif alloc_sf_attr.first().value.lower().strip() not in sf_data:
            # only consider active allocations for not_in_starfish, as there could be archived allocations that are not in Starfish anymore
            if alloc.status.name.lower() == "active":
                not_in_starfish.append(alloc)
    
    vol_path_allocation_attributes = set(list(AllocationAttribute.objects.filter(
        allocation__in=storage_allocations, 
        allocation_attribute_type__name="sf_vol_path").values_list('value', flat=True)))
    vol_path_allocation_attributes = {i.lower().strip() for i in vol_path_allocation_attributes}
    missing_from_coldfront = sf_data - vol_path_allocation_attributes
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
        return get_csv(data, filename="starfish_diff_report.csv")
    return TemplateResponse(request, "tufts_local/sf_report.html", {"volumes": volumes,
                                                                    "missing_starfish_attribute": missing_sf_attribute,
                                                                    "not_in_starfish": not_in_starfish,
                                                                    "missing_from_coldfront": missing_from_coldfront 
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
def no_cost_quotas_report(request):
    if request.method != "GET":
        return TemplateResponse("tufts_local/no_cost_quotas_report.html", {"message": f"unsupported method {request.method}"}, status=400)
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
           return TemplateResponse(request, "tufts_local/no_cost_quotas_report.html", {"message": data['errors'], "allocations": data['allocations'], "shared_allotments": data['shared_allotments']})
    else:
        data = billing_utils.no_cost_quotas_report(user=request.user.username)
        return TemplateResponse(request, "tufts_local/no_cost_quotas_report.html", {"message": data['errors'], "allocations": data['allocations'], "shared_allotments": data['shared_allotments']})


@login_required
def billing_code_audit(request):
    if request.method != "GET":
        return TemplateResponse("tufts_local/billing_code_audit.html", {"message": f"unsupported method {request.method}"}, status=400)
    if request.user.is_superuser:
        form = ReportFilterForm(request.GET)
        if form.is_valid():
            if form.cleaned_data['username']:
                data = billing_utils.billing_code_audit(user=form.cleaned_data['username'])
            else:
                data = billing_utils.billing_code_audit()

            if request.GET.get("format") == "json":
                return JsonResponse(data, status=200)
            else:
               return TemplateResponse(request, "tufts_local/billing_code_audit.html", {"missing_billing_code": data['missing_billing_code'], "charge_report": data['charge_report'], "total_cost": data['total_cost'], "month": data['month'], "form": form})
        else:
            return TemplateResponse(request, "tufts_local/billing_code_audit.html", {"message": "Invalid form data."}, status=400)
    else:
        data = billing_utils.billing_code_audit(user=request.user.username)
        return TemplateResponse(request, "tufts_local/billing_code_audit.html", {"missing_billing_code": data['missing_billing_code'], "charge_report": data['charge_report'], "total_cost": data['total_cost'], "month": data['month']})
