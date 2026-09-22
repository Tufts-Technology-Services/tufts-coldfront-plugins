# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest.mock import patch

from tufts_local.forms import UpdateProjectOwnerForm


class TestUpdateProjectOwnerForm:
    def test_valid_data_is_normalized(self):
        with patch('tufts_local.forms.project_exists', return_value=True) as mock_exists:
            form = UpdateProjectOwnerForm(data={'project_key': 'ABC', 'new_owner': ' JDoe '})

            assert form.is_valid(), form.errors
            assert form.cleaned_data['project_key'] == 'abc'
            assert form.cleaned_data['new_owner'] == 'jdoe'
            mock_exists.assert_called_once_with('abc')

    def test_unknown_project_key_is_invalid(self):
        with patch('tufts_local.forms.project_exists', return_value=False):
            form = UpdateProjectOwnerForm(data={'project_key': 'missing', 'new_owner': 'jdoe'})

            assert not form.is_valid()
            assert "No project found with key 'missing'." in form.errors['project_key']

    def test_missing_new_owner_is_invalid(self):
        with patch('tufts_local.forms.project_exists', return_value=True):
            form = UpdateProjectOwnerForm(data={'project_key': 'abc', 'new_owner': ''})

            assert not form.is_valid()
            assert 'new_owner' in form.errors

    def test_missing_project_key_is_invalid(self):
        form = UpdateProjectOwnerForm(data={'project_key': '', 'new_owner': 'jdoe'})

        assert not form.is_valid()
        assert 'project_key' in form.errors
