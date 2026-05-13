from django.urls import path

from . import views


urlpatterns = [
    path(
        'project-update-user-role/',
        views.project_update_user_role,
        name='project-update-user-role',
    )
]