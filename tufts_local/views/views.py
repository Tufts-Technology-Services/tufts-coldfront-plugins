import json
import logging
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST, require_GET
from django_q.tasks import async_task
from coldfront.core.project.models import ProjectUser, ProjectUserRoleChoice
from coldfront.core.allocation.models import Allocation
from tufts_local import utils
from tufts_local.tasks import update_sf_approver_tags

logger = logging.getLogger(__name__)


@login_required
@require_POST
def project_update_user_role(request):
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
   


@login_required
@require_GET
def project_get_email_notification(request, project_user_id):
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



@login_required
@require_GET
def utln_autocomplete(request):
    if request.user.is_superuser is False:
        return JsonResponse({"message": "not allowed"}, status=403)

    query = request.GET.get("query", "")
    # Placeholder for actual autocomplete logic, e.g., querying an external service
    suggestions = utils.user_autocomplete(query)  # Replace with actual suggestions based on the query
    return JsonResponse({"suggestions": suggestions}, status=200)


@login_required
@require_POST
def add_user_to_coldfront(request):
    if request.user.is_superuser is False:
        return JsonResponse({"message": "not allowed"}, status=403)

    data = json.loads(request.body)
    username = data.get("username", "").strip()

    if not username:
        return JsonResponse({"message": "username is required"}, status=400)
    try:
        _ = utils.create_user(username)  # Replace with actual user creation logic
        return JsonResponse({"message": "user created successfully"}, status=201)

    except Exception as e:
        return JsonResponse({"message": f"error occurred: {str(e)}"}, status=500)


@login_required
@require_GET
def storage_allocation_history(request, allocation_id):
    # Placeholder for actual storage allocation history logic
    if not allocation_id:
        return JsonResponse({"message": "allocation_id is required"}, status=400)
    if not (request.user.is_superuser or utils.user_has_allocation_access(request.user, allocation_id)):
        return JsonResponse({"message": "not allowed"}, status=403)
    try:
        get_object_or_404(Allocation, id=allocation_id)  # Ensure the allocation exists
        history, usage_history = utils.get_storage_allocation_history(allocation_id)
        return JsonResponse(utils.history_chart_format(history, usage_history), status=200)
    except Exception as e:
        return JsonResponse({"message": f"error occurred: {str(e)}"}, status=500)
