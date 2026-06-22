from django.views.generic import CreateView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from coldfront.core.project.models import (Project,
                                            ProjectUser,
                                            ProjectUserRoleChoice,
                                            ProjectUserStatusChoice,
                                            ProjectAttribute,
                                            ProjectAttributeType,
                                            ProjectStatusChoice)
from coldfront.core.project.signals import project_new, project_activate_user


from tufts_local.forms import AdminProjectCreationForm, RequiredProjectAttributeForm


class AdminProjectCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Project
    template_name = "tufts_local/project_create_form.html"
    form_class = AdminProjectCreationForm

    def test_func(self):
        """UserPassesTestMixin Tests"""
        if self.request.user.is_superuser:
            return True
        return False
    
    def get_context_data(self, **kwargs):
        """Inject the secondary form into the template context."""
        context = super().get_context_data(**kwargs)
        if 'required_project_attribute_form' not in context:
            # Pass instance data if validation failed and we are redisplaying
            context['required_project_attribute_form'] = RequiredProjectAttributeForm(self.request.POST or None)
        return context

    def post(self, request, *args, **kwargs):
        """Handle POST data for both forms."""
        self.object = None  # Explicitly set object to None for CreateView flow
        
        # Instantiate both forms with the POST payload
        parent_form = self.get_form()
        child_form = RequiredProjectAttributeForm(request.POST)

        # Check validity of both forms simultaneously
        if parent_form.is_valid() and child_form.is_valid():
            return self.form_valid(parent_form, child_form)
        else:
            return self.form_invalid(parent_form, child_form)

    def form_valid(self, parent_form, child_form):
        """Save database records when validation succeeds."""
        # Save the parent instance first
        project_obj = parent_form.save(commit=False)
        parent_form.instance.status = ProjectStatusChoice.objects.get(name="Active")
        parent_form.instance.title = child_form.cleaned_data['project_key'].lower()
        parent_form.instance.pi = child_form.cleaned_data['owner']
        project_obj.save()
        self.object = project_obj
        
        project_user = ProjectUser.objects.create(
            user=project_obj.pi,
            project=project_obj,
            role=ProjectUserRoleChoice.objects.get(name="Manager"),
            status=ProjectUserStatusChoice.objects.get(name="Active"),
        )

        ProjectAttribute.objects.create(
            project=project_obj,
            proj_attr_type=ProjectAttributeType.objects.get(name='Project Key'),
            value=child_form.cleaned_data['project_key']
        )
        
        ProjectAttribute.objects.create(
            project=project_obj,
            proj_attr_type=ProjectAttributeType.objects.get(name='Group'),
            value=child_form.cleaned_data['group']
        )

        ProjectAttribute.objects.create(
            project=project_obj,
            proj_attr_type=ProjectAttributeType.objects.get(name='users_managed'),
            value='Yes'
        )
        # project signals
        project_new.send(sender=self.__class__, project_pk=project_obj.id, request_user=self.request.user.username)
        project_activate_user.send(sender=self.__class__, project_user_pk=project_user.id, request_user=self.request.user.username)

        return super().form_valid(parent_form)

    def form_invalid(self, parent_form, child_form):
        """Redisplay the forms with error context if any form fails validation."""
        return self.render_to_response(
            self.get_context_data(form=parent_form, required_project_attribute_form=child_form)
        )
