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
    path(
        'billing-code-report/',
        views.billing_code_report,
        name='billing-code-report',
    ),
    path(
        'cost-preview-report/',
        views.charge_report,
        name='cost-preview-report',
    ),
    path(
        'add-user-to-coldfront/',
        views.add_user_to_coldfront,
        name='add-user-to-coldfront',
    ),
    path(
        'local-login-as-user/<username>/',
        views.login_as_user_view,
        name='local-login-as-user',
    ),
    path(
        'not-updated-report/',
        views.not_updated_report,
        name='not-updated-report',
    ),
    path(
        'oversubscribed-allotments-report/',
        views.oversubscribed_allotments_report,
        name='oversubscribed-allotments-report',
    ),
]
