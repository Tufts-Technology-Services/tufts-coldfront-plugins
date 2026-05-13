from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from coldfront.core.project.models import ProjectUser, ProjectUserRoleChoice


@login_required
def project_update_user_role(request):
    if request.method == "POST":
        data = request.POST
        project_user_obj = get_object_or_404(ProjectUser, pk=data.get("user_project_id"))

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
            return HttpResponse("not allowed", status=403)
        else:
            role_name = data.get("role")
            if role_name:
                project_user_role = ProjectUserRoleChoice.objects.get(name=role_name)
                if project_user_role:
                    project_user_obj.role = project_user_role
                    project_user_obj.save()
                    return HttpResponse("role updated", status=200)
                else:
                    return HttpResponse("invalid role specified", status=400)
            else:
                return HttpResponse("no role specified", status=400)
    else:
        return HttpResponse("no POST", status=400)
