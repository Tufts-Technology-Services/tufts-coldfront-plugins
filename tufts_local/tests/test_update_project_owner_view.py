from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory

from tufts_local.utils import UserNotFoundError
from tufts_local.views import UpdateProjectOwnerView


def make_user(is_superuser=False):
    user = MagicMock(name='user')
    user.is_authenticated = True
    user.is_superuser = is_superuser
    return user


@pytest.fixture
def rf():
    return RequestFactory()


@pytest.fixture
def view():
    return UpdateProjectOwnerView.as_view()


class TestUpdateProjectOwnerViewAccess:
    def test_anonymous_user_redirects_to_login(self, rf, view):
        request = rf.get('/update-project-owner/')
        request.user = AnonymousUser()

        response = view(request)

        assert response.status_code == 302
        assert 'login' in response.url

    def test_non_superuser_denied(self, rf, view):
        request = rf.get('/update-project-owner/')
        request.user = make_user(is_superuser=False)

        with pytest.raises(PermissionDenied):
            view(request)

    def test_superuser_can_view_form(self, rf, view):
        request = rf.get('/update-project-owner/')
        request.user = make_user(is_superuser=True)

        response = view(request)

        assert response.status_code == 200


class TestUpdateProjectOwnerViewSubmit:
    def test_invalid_project_key_redisplays_form(self, rf, view):
        request = rf.post('/update-project-owner/', {'project_key': 'missing', 'new_owner': 'jdoe'})
        request.user = make_user(is_superuser=True)

        with patch('tufts_local.forms.project_exists', return_value=False):
            response = view(request)

        assert response.status_code == 200
        assert "No project found with key 'missing'." in response.context_data['form'].errors['project_key']

    def test_missing_new_owner_redisplays_form(self, rf, view):
        request = rf.post('/update-project-owner/', {'project_key': 'abc', 'new_owner': ''})
        request.user = make_user(is_superuser=True)

        with patch('tufts_local.forms.project_exists', return_value=True):
            response = view(request)

        assert response.status_code == 200
        assert 'new_owner' in response.context_data['form'].errors

    @pytest.mark.urls('tufts_local.urls')
    @patch('tufts_local.views.update_project_owner_view.messages')
    @patch('tufts_local.views.update_project_owner_view.update_project_owner')
    def test_success_updates_owner_and_redirects(self, mock_update, mock_messages, rf, view):
        mock_update.return_value = MagicMock(title='abc')
        request = rf.post('/update-project-owner/', {'project_key': 'ABC', 'new_owner': 'JDoe'})
        request.user = make_user(is_superuser=True)

        with patch('tufts_local.forms.project_exists', return_value=True):
            response = view(request)

        assert response.status_code == 302
        mock_update.assert_called_once_with('abc', 'jdoe')
        mock_messages.success.assert_called_once()

    @patch('tufts_local.views.update_project_owner_view.messages')
    @patch('tufts_local.views.update_project_owner_view.update_project_owner')
    def test_user_not_found_error_redisplays_form(self, mock_update, mock_messages, rf, view):
        mock_update.side_effect = UserNotFoundError('No user found for jdoe')
        request = rf.post('/update-project-owner/', {'project_key': 'abc', 'new_owner': 'jdoe'})
        request.user = make_user(is_superuser=True)

        with patch('tufts_local.forms.project_exists', return_value=True):
            response = view(request)

        assert response.status_code == 200
        assert 'No user found for jdoe' in response.context_data['form'].errors['new_owner']
        mock_messages.success.assert_not_called()

    @patch('tufts_local.views.update_project_owner_view.messages')
    @patch('tufts_local.views.update_project_owner_view.update_project_owner')
    def test_generic_error_redisplays_form(self, mock_update, mock_messages, rf, view):
        mock_update.side_effect = Exception('boom')
        request = rf.post('/update-project-owner/', {'project_key': 'abc', 'new_owner': 'jdoe'})
        request.user = make_user(is_superuser=True)

        with patch('tufts_local.forms.project_exists', return_value=True):
            response = view(request)

        assert response.status_code == 200
        assert 'boom' in response.context_data['form'].errors['__all__']
        mock_messages.success.assert_not_called()
