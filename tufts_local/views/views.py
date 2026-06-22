from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from coldfront.core.project.models import ProjectUser, ProjectUserRoleChoice
from tufts_local import utils


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
