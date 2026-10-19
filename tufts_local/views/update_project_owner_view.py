# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.urls import reverse_lazy
from django.views.generic import FormView
from django_q.tasks import async_task

from tufts_local.forms import UpdateProjectOwnerForm
from tufts_local.tasks import set_sf_owner_tag, update_sf_approver_tags
from tufts_local.utils import UserNotFoundError, update_project_owner

logger = logging.getLogger(__name__)


class UpdateProjectOwnerView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    template_name = 'tufts_local/update_project_owner_form.html'
    form_class = UpdateProjectOwnerForm
    success_url = reverse_lazy('update-project-owner')

    def test_func(self):
        """UserPassesTestMixin Tests"""
        return self.request.user.is_superuser

    def form_valid(self, form):
        project_key = form.cleaned_data['project_key']
        new_owner = form.cleaned_data['new_owner']
        try:
            project = update_project_owner(project_key, new_owner)
        except UserNotFoundError as e:
            form.add_error('new_owner', str(e))
            return self.form_invalid(form)
        except Exception as e:
            form.add_error(None, str(e))
            return self.form_invalid(form)

        messages.success(self.request, f"Updated owner of project '{project.title}' to '{new_owner}'.")
        self.queue_starfish_tags(project)
        return super().form_valid(form)

    def queue_starfish_tags(self, project):
        """Queue the Starfish tag updates for the project's new owner.

        Queued, not run here: they call Starfish's API, so a slow or failing Starfish must neither hold
        up the page nor make the owner change, which has already happened, look as if it failed. If even
        queueing fails (the broker is down, say), the change still stands, with a warning.
        """
        try:
            for allocation in project.allocation_set.filter(status__name='Active'):
                # built per call: async_task pops task_name out of the dict it's given
                async_task(
                    set_sf_owner_tag,
                    allocation.id,
                    q_options={'task_name': f'sf_owner_tag_{allocation.id}', 'group': 'starfish'},
                )
            async_task(
                update_sf_approver_tags,
                project.id,
                q_options={'task_name': f'sf_approver_tags_{project.id}', 'group': 'starfish'},
            )
        except Exception:
            logger.exception(f'Could not queue the Starfish tag updates for project {project.id}')
            messages.warning(
                self.request,
                "The owner was updated, but the Starfish tag updates for it couldn't be queued, so "
                "the project's Starfish tags need updating separately.",
            )
