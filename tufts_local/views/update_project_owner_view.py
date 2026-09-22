# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.urls import reverse_lazy
from django.views.generic import FormView

from tufts_local.forms import UpdateProjectOwnerForm
from tufts_local.utils import UserNotFoundError, update_project_owner


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
        return super().form_valid(form)
