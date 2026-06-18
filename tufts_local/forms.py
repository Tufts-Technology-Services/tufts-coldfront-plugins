from django import forms
from coldfront.core.project.models import Project, ProjectAttribute
from tufts_local.utils import create_user, entry_exists


class AdminProjectCreationForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ["description"]


class RequiredProjectAttributeForm(forms.Form):
    owner = forms.CharField(max_length=10, required=True, disabled=False)
    project_key = forms.SlugField(max_length=50, required=True, disabled=False)
    group = forms.SlugField(max_length=50, required=True, disabled=False)

    def clean(self):
        cleaned_data = super().clean()
        owner = cleaned_data.get("owner")
        if not owner:
            raise forms.ValidationError("Owner is required.")
        try:            
            owner_user = create_user(owner)  # import user from AD and create if not exists
            cleaned_data["owner"] = owner_user
        except Exception as e:
            raise forms.ValidationError(f"{str(e)}")
        
        project_key = cleaned_data.get("project_key").lower()
        if ProjectAttribute.objects.filter(value__iexact=project_key, proj_attr_type__name='Project Key').exists():
            raise forms.ValidationError(f"A project with the key '{project_key}' already exists.")
        # validation for group 
        group = cleaned_data.get("group").lower()
        # group should be unique across Active Directory objects
        if ProjectAttribute.objects.filter(value__iexact=group, proj_attr_type__name='Group').exists():
            raise forms.ValidationError(f"A group with the name '{group}' already exists.")
        try:
            if entry_exists(group):
                raise forms.ValidationError(f"Existing Active Directory entry for name '{group}'. Please choose a different name.")
        except Exception as e:
            raise forms.ValidationError(f"Invalid group name: {str(e)}")
        return cleaned_data