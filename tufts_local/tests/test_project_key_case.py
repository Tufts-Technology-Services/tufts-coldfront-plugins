# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
New projects get a lowercase Project Key, whatever case it's typed in.

The key names the project's storage (every allocation blueprint builds its paths from it) and is
used as its group, so new ones are always lowercase. Older projects' shares on VAST can be mixed
case, but those paths live in the allocations' own attributes, not in the key.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from coldfront.core.field_of_science.models import FieldOfScience
from coldfront.core.project.models import (
    AttributeType,
    Project,
    ProjectAttribute,
    ProjectAttributeType,
    ProjectStatusChoice,
)

from tufts_local.forms import RequiredProjectAttributeForm
from tufts_local.utils import create_tufts_project


@pytest.fixture
def owner(db):
    return get_user_model().objects.create(username="owner01")


@pytest.fixture
def no_ad(owner):
    """The form's AD lookups: the owner exists, and no AD entry has the group's name."""
    with (
        patch("tufts_local.forms.create_user", return_value=owner),
        patch("tufts_local.forms.entry_exists", return_value=False) as entry_exists,
    ):
        yield entry_exists


@pytest.fixture
def project_setup(db, roles, statuses):
    """The choices and attribute types project creation looks up."""
    FieldOfScience.objects.get_or_create(pk=FieldOfScience.DEFAULT_PK, defaults={"description": "Other"})
    ProjectStatusChoice.objects.get_or_create(name="Active")
    text = AttributeType.objects.get_or_create(name="Text")[0]
    for name in ("Project Key", "Group", "users_managed"):
        ProjectAttributeType.objects.get_or_create(name=name, attribute_type=text)


def project_attribute(project, name):
    return ProjectAttribute.objects.get(project=project, proj_attr_type__name=name).value


class TestRequiredProjectAttributeForm:
    def test_the_key_is_lowercased(self, no_ad):
        form = RequiredProjectAttributeForm(data={"owner": "owner01", "project_key": "SmithLab"})

        assert form.is_valid(), form.errors
        assert form.cleaned_data["project_key"] == "smithlab"

    def test_ad_is_checked_for_the_lowercase_name(self, no_ad):
        RequiredProjectAttributeForm(data={"owner": "owner01", "project_key": "SmithLab"}).is_valid()

        no_ad.assert_called_once_with("smithlab")

    def test_a_key_differing_only_in_case_from_an_existing_one_is_refused(self, no_ad, project, project_setup):
        ProjectAttribute.objects.create(
            project=project, proj_attr_type=ProjectAttributeType.objects.get(name="Project Key"), value="smithlab"
        )

        form = RequiredProjectAttributeForm(data={"owner": "owner01", "project_key": "SMITHLAB"})

        assert not form.is_valid()
        assert "A project with the key 'smithlab' already exists." in form.non_field_errors()


@pytest.mark.urls("tufts_local.tests.urls")
class TestAdminProjectCreateView:
    def test_a_mixed_case_key_is_saved_lowercase(self, client, no_ad, project_setup):
        client.force_login(get_user_model().objects.create(username="rdms_admin", is_superuser=True))

        response = client.post(
            reverse("admin-project-create"),
            {"description": "Modelling ocean currents.", "owner": "owner01", "project_key": "SmithLab"},
        )

        assert response.status_code == 302
        project = Project.objects.get()
        assert project.title == "smithlab"
        assert project_attribute(project, "Project Key") == "smithlab"
        assert project_attribute(project, "Group") == "smithlab"


class TestCreateTuftsProject:
    def test_a_mixed_case_key_is_saved_lowercase(self, project_setup):
        project = create_tufts_project("SmithLab", "pi01", group="smithlab")

        assert project.title == "smithlab"
        assert project_attribute(project, "Project Key") == "smithlab"

    def test_an_existing_key_in_another_case_returns_that_project(self, project_setup):
        existing = create_tufts_project("smithlab", "pi01")

        assert create_tufts_project("SmithLab", "pi01") == existing
        assert Project.objects.count() == 1
