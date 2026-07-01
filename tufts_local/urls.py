from django.urls import path

from tufts_local import views


urlpatterns = [
    path(
        'project-update-user-role/',
        views.project_update_user_role,
        name='project-update-user-role',
    ),
    path(
       'project-user-get-email-notification/<project_user_id>/',
       views.project_get_email_notification,
       name='project-user-get-email-notification'
    ),
    path(
        'admin-project-create/',
        views.AdminProjectCreateView.as_view(),
        name='admin-project-create',
    ),
    path(
        'utln-autocomplete/',
        views.utln_autocomplete,
        name='utln-autocomplete',
    ),
    path(
        'sf-report/',
        views.sf_report,
        name='sf-report',
    ),
    path(
        'no-cost-quotas-report/',
        views.no_cost_quotas_report,
        name='no-cost-quotas-report',
    ),
]
