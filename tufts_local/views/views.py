from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, TemplateResponse
from django.shortcuts import get_object_or_404
from coldfront.core.project.models import ProjectUser, ProjectUserRoleChoice
from coldfront.core.allocation.models import Allocation, AllocationAttribute
from coldfront.core.resource.models import Resource
from tufts_local import utils
from tufts_local.starfish_utils import get_starfish_usage_data_by_volume


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
                    return JsonResponse({"message": "role updated"}, status=200)
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
    storage_allocations = Allocation.objects.filter(resource__in=storage)
    missing_sf_attribute = []
    not_in_starfish = []
    for alloc in storage_allocations:
        alloc_sf_attr = AllocationAttribute.objects.filter(allocation=alloc, allocation_attribute_type__name="sf_vol_path")
        if not alloc_sf_attr.exists():
            missing_sf_attribute.append(alloc)
        elif alloc_sf_attr.first().value.lower().strip() not in sf_data:
            not_in_starfish.append(alloc)
    
    vol_path_allocation_attributes = set(list(AllocationAttribute.objects.filter(
        allocation__in=storage_allocations, 
        allocation_attribute_type__name="sf_vol_path").values_list('value', flat=True)))
    vol_path_allocation_attributes = {i.lower().strip() for i in vol_path_allocation_attributes}
    missing_from_coldfront = sf_data - vol_path_allocation_attributes
    return TemplateResponse(request, "tufts_local/sf_report.html", {"volumes": volumes,
                                                                    "missing_starfish_attribute": missing_sf_attribute,
                                                                    "not_in_starfish": not_in_starfish,
                                                                    "missing_from_coldfront": missing_from_coldfront 
                                                                    })
