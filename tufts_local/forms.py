from django import forms

from coldfront.core.project.models import Project, ProjectAttribute

from tufts_local.utils import create_user, entry_exists, project_exists


class AdminProjectCreationForm(forms.ModelForm):
    description = forms.CharField(
        initial=None,
        required=True,
        help_text='Provide a brief description of the project. This will be visible to all users. Must be 10 characters or longer.',
        widget=forms.Textarea(),
    )

    class Meta:
        model = Project
        fields = ['description']


class RequiredProjectAttributeForm(forms.Form):
    owner = forms.CharField(max_length=10, required=True, disabled=False, label='Project Owner (utln)')
    project_key = forms.SlugField(max_length=50, required=True, disabled=False)
    # group = forms.SlugField(max_length=50, required=True, disabled=False)

    def clean(self):
        cleaned_data = super().clean()
        owner = cleaned_data.get('owner')
        if not owner:
            raise forms.ValidationError('Owner is required.')
        try:
            owner_user = create_user(owner)  # import user from AD and create if not exists
            cleaned_data['owner'] = owner_user
        except Exception as e:
            raise forms.ValidationError(f'{str(e)}')

        project_key = cleaned_data.get('project_key').lower()
        if ProjectAttribute.objects.filter(value__iexact=project_key, proj_attr_type__name='Project Key').exists():
            raise forms.ValidationError(f"A project with the key '{project_key}' already exists.")
        # validation for group
        group = project_key  # Use project_key as the group name
        # group should be unique across Active Directory objects
        if ProjectAttribute.objects.filter(value__iexact=group, proj_attr_type__name='Group').exists():
            raise forms.ValidationError(f"A group with the name '{group}' already exists.")
        try:
            if entry_exists(group):
                raise forms.ValidationError(
                    f"Existing Active Directory entry for name '{group}'. Please choose a different name."
                )
        except Exception as e:
            raise forms.ValidationError(f'Invalid group name: {str(e)}')
        return cleaned_data


class UpdateProjectOwnerForm(forms.Form):
    project_key = forms.SlugField(max_length=50, required=True, label='Project Key')
    new_owner = forms.CharField(max_length=10, required=True, label='New Project Owner (utln)')

    def clean_project_key(self):
        project_key = self.cleaned_data['project_key'].lower()
        if not project_exists(project_key):
            raise forms.ValidationError(f"No project found with key '{project_key}'.")
        return project_key

    def clean_new_owner(self):
        return self.cleaned_data['new_owner'].lower().strip()


class ReportFilterForm(forms.Form):
    username = forms.CharField(max_length=50, required=False, label='Username (utln)')
    project_key = forms.SlugField(max_length=50, required=False, label='Project Key')
    project_title = forms.CharField(max_length=100, required=False, label='Project Title')
    billing_code = forms.CharField(max_length=50, required=False, label='Billing Code')


class TaskFilterForm(forms.Form):
    name = forms.CharField(max_length=100, required=False, label='Task Name')
    group = forms.ChoiceField(required=False, label='Group')

    def __init__(self, *args, groups=(), **kwargs):
        super().__init__(*args, **kwargs)
        # group choices come from the groups actually present in the queue, so they can't be declared statically
        self.fields['group'].choices = [('', 'All')] + [(g, g) for g in groups]

    def clean_name(self):
        return self.cleaned_data['name'].strip()
