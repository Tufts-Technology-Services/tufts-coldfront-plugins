# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from django.apps import apps
from django.urls import path

from tufts_local import views

urlpatterns = [
    path(
        "project-update-user-role/",
        views.project_update_user_role,
        name="project-update-user-role",
    ),
    path(
        "project-user-get-email-notification/<project_user_id>/",
        views.project_get_email_notification,
        name="project-user-get-email-notification",
    ),
    path(
        "admin-project-create/",
        views.AdminProjectCreateView.as_view(),
        name="admin-project-create",
    ),
    path(
        "utln-autocomplete/",
        views.utln_autocomplete,
        name="utln-autocomplete",
    ),
    path(
        "sf-report/",
        views.sf_report,
        name="sf-report",
    ),
    path(
        "add-user-to-coldfront/",
        views.add_user_to_coldfront,
        name="add-user-to-coldfront",
    ),
    path(
        "local-login-as-user/<username>/",
        views.login_as_user_view,
        name="local-login-as-user",
    ),
    path(
        "not-updated-report/",
        views.not_updated_report,
        name="not-updated-report",
    ),
    path(
        "storage-allocation-history/<allocation_id>/",
        views.storage_allocation_history,
        name="storage-allocation-history",
    ),
    path(
        "update-project-owner/",
        views.UpdateProjectOwnerView.as_view(),
        name="update-project-owner",
    ),
    path(
        "storage-status-change-review/",
        views.storage_status_change_review,
        name="storage-status-change-review",
    ),
    path(
        "task-report/",
        views.task_report,
        name="task-report",
    ),
    path(
        # django-q task ids are 32-character strings, not integers
        "task-acknowledge/<str:task_id>/",
        views.acknowledge_task,
        name="task-acknowledge",
    ),
    path(
        "task-acknowledge-bulk/",
        views.acknowledge_tasks_in_bulk,
        name="task-acknowledge-bulk",
    ),
    path(
        "task-unacknowledge/<str:task_id>/",
        views.unacknowledge_task,
        name="task-unacknowledge",
    ),
    path(
        "history/",
        views.project_history_search,
        name="project-history-search",
    ),
    path(
        "history/project/<int:pk>/",
        views.project_history,
        name="project-history",
    ),
    path(
        "alerts/",
        views.alert_list,
        name="alert-list",
    ),
    path(
        "alert-indicator/",
        views.alert_indicator,
        name="alert-indicator",
    ),
]

# coldfront_billing is optional, so its views are only routed where it's installed
if apps.is_installed("coldfront_billing"):
    from tufts_local.views.billing_alerts import acknowledge_allotment_failure

    urlpatterns.append(
        path(
            "allotment-failure-acknowledge/<int:pk>/",
            acknowledge_allotment_failure,
            name="allotment-failure-acknowledge",
        )
    )
