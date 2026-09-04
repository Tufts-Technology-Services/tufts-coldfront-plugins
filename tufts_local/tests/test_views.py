import json
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from tufts_local.views import views


def make_user(is_superuser=False):
    user = MagicMock(name='user')
    user.is_authenticated = True
    user.is_superuser = is_superuser
    return user


@pytest.fixture
def rf():
    return RequestFactory()


class TestProjectUpdateUserRole:
    def test_anonymous_user_redirects_to_login(self, rf):
        request = rf.post('/project-update-user-role/')
        request.user = AnonymousUser()

        response = views.project_update_user_role(request)

        assert response.status_code == 302

    def test_get_not_allowed(self, rf):
        request = rf.get('/project-update-user-role/')
        request.user = make_user()

        response = views.project_update_user_role(request)

        assert response.status_code == 405

    @patch('tufts_local.views.views.approver_at_least')
    @patch('tufts_local.views.views.get_object_or_404')
    def test_not_allowed(self, mock_get_object, mock_approver, rf):
        mock_get_object.return_value = MagicMock()
        mock_approver.return_value = False
        request = rf.post('/project-update-user-role/', {'user_project_id': '1', 'role': 'Manager'})
        request.user = make_user()

        response = views.project_update_user_role(request)

        assert response.status_code == 403
        assert json.loads(response.content) == {'message': 'not allowed'}

    @patch('tufts_local.views.views.approver_at_least')
    @patch('tufts_local.views.views.get_object_or_404')
    def test_no_role_specified(self, mock_get_object, mock_approver, rf):
        mock_get_object.return_value = MagicMock()
        mock_approver.return_value = True
        request = rf.post('/project-update-user-role/', {'user_project_id': '1'})
        request.user = make_user()

        response = views.project_update_user_role(request)

        assert response.status_code == 400
        assert json.loads(response.content) == {'message': 'no role specified'}

    @patch('tufts_local.views.views.ProjectUserRoleChoice')
    @patch('tufts_local.views.views.approver_at_least')
    @patch('tufts_local.views.views.get_object_or_404')
    def test_invalid_role(self, mock_get_object, mock_approver, mock_role_choice, rf):
        mock_get_object.return_value = MagicMock()
        mock_approver.return_value = True
        mock_role_choice.objects.get.return_value = None
        request = rf.post('/project-update-user-role/', {'user_project_id': '1', 'role': 'Bogus'})
        request.user = make_user()

        response = views.project_update_user_role(request)

        assert response.status_code == 400
        assert json.loads(response.content) == {'message': 'invalid role specified'}

    @patch('tufts_local.views.views.async_task')
    @patch('tufts_local.views.views.ProjectUserRoleChoice')
    @patch('tufts_local.views.views.approver_at_least')
    @patch('tufts_local.views.views.get_object_or_404')
    def test_success(self, mock_get_object, mock_approver, mock_role_choice, mock_async_task, rf):
        project_user_obj = MagicMock()
        project_user_obj.project.id = 42
        mock_get_object.return_value = project_user_obj
        mock_approver.return_value = True
        role = MagicMock()
        mock_role_choice.objects.get.return_value = role
        mock_async_task.return_value = 'task-id-123'

        request = rf.post('/project-update-user-role/', {'user_project_id': '1', 'role': 'Manager'})
        request.user = make_user()

        response = views.project_update_user_role(request)

        assert response.status_code == 200
        assert json.loads(response.content) == {'message': 'role updated', 'task_id': 'task-id-123'}
        assert project_user_obj.role == role
        project_user_obj.save.assert_called_once()
        mock_async_task.assert_called_once_with(views.update_sf_approver_tags, 42)


class TestProjectGetEmailNotification:
    def test_post_not_allowed(self, rf):
        request = rf.post('/project-user-get-email-notification/1/')
        request.user = make_user()

        response = views.project_get_email_notification(request, project_user_id=1)

        assert response.status_code == 405

    @patch('tufts_local.views.views.get_object_or_404')
    def test_allowed_as_pi(self, mock_get_object, rf):
        user = make_user()
        project_user_obj = MagicMock()
        project_user_obj.project.pi = user
        project_user_obj.project.projectuser_set.filter.return_value.exists.return_value = False
        project_user_obj.user = MagicMock()
        mock_get_object.return_value = project_user_obj

        request = rf.get('/project-user-get-email-notification/1/')
        request.user = user

        response = views.project_get_email_notification(request, project_user_id=1)

        assert response.status_code == 200
        assert json.loads(response.content) == {'enable_notifications': True}

    @patch('tufts_local.views.views.get_object_or_404')
    def test_allowed_as_active_manager(self, mock_get_object, rf):
        user = make_user()
        project_user_obj = MagicMock()
        project_user_obj.project.pi = MagicMock()
        project_user_obj.project.projectuser_set.filter.return_value.exists.return_value = True
        project_user_obj.user = MagicMock()
        mock_get_object.return_value = project_user_obj

        request = rf.get('/project-user-get-email-notification/1/')
        request.user = user

        response = views.project_get_email_notification(request, project_user_id=1)

        assert response.status_code == 200
        project_user_obj.project.projectuser_set.filter.assert_called_once_with(
            user=user, role__name='Manager', status__name='Active'
        )

    @patch('tufts_local.views.views.get_object_or_404')
    def test_allowed_as_subject_user(self, mock_get_object, rf):
        user = make_user()
        project_user_obj = MagicMock()
        project_user_obj.project.pi = MagicMock()
        project_user_obj.project.projectuser_set.filter.return_value.exists.return_value = False
        project_user_obj.user = user
        mock_get_object.return_value = project_user_obj

        request = rf.get('/project-user-get-email-notification/1/')
        request.user = user

        response = views.project_get_email_notification(request, project_user_id=1)

        assert response.status_code == 200

    @patch('tufts_local.views.views.get_object_or_404')
    def test_allowed_as_superuser(self, mock_get_object, rf):
        project_user_obj = MagicMock()
        project_user_obj.project.pi = MagicMock()
        project_user_obj.project.projectuser_set.filter.return_value.exists.return_value = False
        project_user_obj.user = MagicMock()
        mock_get_object.return_value = project_user_obj

        request = rf.get('/project-user-get-email-notification/1/')
        request.user = make_user(is_superuser=True)

        response = views.project_get_email_notification(request, project_user_id=1)

        assert response.status_code == 200

    @patch('tufts_local.views.views.get_object_or_404')
    def test_not_allowed(self, mock_get_object, rf):
        project_user_obj = MagicMock()
        project_user_obj.project.pi = MagicMock()
        project_user_obj.project.projectuser_set.filter.return_value.exists.return_value = False
        project_user_obj.user = MagicMock()
        mock_get_object.return_value = project_user_obj

        request = rf.get('/project-user-get-email-notification/1/')
        request.user = make_user(is_superuser=False)

        response = views.project_get_email_notification(request, project_user_id=1)

        assert response.status_code == 403
        assert json.loads(response.content) == {'message': 'not allowed'}


class TestUtlnAutocomplete:
    def test_not_superuser(self, rf):
        request = rf.get('/utln-autocomplete/', {'query': 'abc'})
        request.user = make_user(is_superuser=False)

        response = views.utln_autocomplete(request)

        assert response.status_code == 403

    def test_post_not_allowed(self, rf):
        request = rf.post('/utln-autocomplete/')
        request.user = make_user(is_superuser=True)

        response = views.utln_autocomplete(request)

        assert response.status_code == 405

    @patch('tufts_local.views.views.utils')
    def test_success(self, mock_utils, rf):
        mock_utils.user_autocomplete.return_value = ['jdoe', 'jsmith']
        request = rf.get('/utln-autocomplete/', {'query': 'j'})
        request.user = make_user(is_superuser=True)

        response = views.utln_autocomplete(request)

        assert response.status_code == 200
        assert json.loads(response.content) == {'suggestions': ['jdoe', 'jsmith']}
        mock_utils.user_autocomplete.assert_called_once_with('j')


class TestAddUserToColdfront:
    def test_not_superuser(self, rf):
        request = rf.post('/add-user-to-coldfront/', data={'username': 'jdoe'}, content_type='application/json')
        request.user = make_user(is_superuser=False)

        response = views.add_user_to_coldfront(request)

        assert response.status_code == 403

    def test_missing_username(self, rf):
        request = rf.post('/add-user-to-coldfront/', data={'username': '  '}, content_type='application/json')
        request.user = make_user(is_superuser=True)

        response = views.add_user_to_coldfront(request)

        assert response.status_code == 400
        assert json.loads(response.content) == {'message': 'username is required'}

    @patch('tufts_local.views.views.utils')
    def test_success(self, mock_utils, rf):
        mock_utils.create_user.return_value = MagicMock()
        request = rf.post('/add-user-to-coldfront/', data={'username': 'jdoe'}, content_type='application/json')
        request.user = make_user(is_superuser=True)

        response = views.add_user_to_coldfront(request)

        assert response.status_code == 201
        mock_utils.create_user.assert_called_once_with('jdoe')

    @patch('tufts_local.views.views.utils')
    def test_error_from_user_creation(self, mock_utils, rf):
        mock_utils.create_user.side_effect = Exception('boom')
        request = rf.post('/add-user-to-coldfront/', data={'username': 'jdoe'}, content_type='application/json')
        request.user = make_user(is_superuser=True)

        response = views.add_user_to_coldfront(request)

        assert response.status_code == 500
        assert json.loads(response.content) == {'message': 'error occurred: boom'}


class TestStorageAllocationHistory:
    def test_missing_allocation_id(self, rf):
        request = rf.get('/storage-allocation-history//')
        request.user = make_user()

        response = views.storage_allocation_history(request, allocation_id='')

        assert response.status_code == 400
        assert json.loads(response.content) == {'message': 'allocation_id is required'}

    @patch('tufts_local.views.views.utils')
    def test_not_allowed(self, mock_utils, rf):
        mock_utils.user_has_allocation_access.return_value = False
        request = rf.get('/storage-allocation-history/5/')
        request.user = make_user(is_superuser=False)

        response = views.storage_allocation_history(request, allocation_id='5')

        assert response.status_code == 403
        assert json.loads(response.content) == {'message': 'not allowed'}

    @patch('tufts_local.views.views.get_object_or_404')
    @patch('tufts_local.views.views.utils')
    def test_success(self, mock_utils, mock_get_object, rf):
        mock_utils.user_has_allocation_access.return_value = True
        mock_utils.get_storage_allocation_history.return_value = (['history'], ['usage'])
        mock_utils.history_chart_format.return_value = {'datasets': []}
        request = rf.get('/storage-allocation-history/5/')
        request.user = make_user(is_superuser=False)

        response = views.storage_allocation_history(request, allocation_id='5')

        assert response.status_code == 200
        assert json.loads(response.content) == {'datasets': []}
        mock_utils.get_storage_allocation_history.assert_called_once_with('5')
        mock_utils.history_chart_format.assert_called_once_with(['history'], ['usage'])

    @patch('tufts_local.views.views.get_object_or_404')
    @patch('tufts_local.views.views.utils')
    def test_superuser_bypasses_access_check(self, mock_utils, mock_get_object, rf):
        mock_utils.get_storage_allocation_history.return_value = ([], [])
        mock_utils.history_chart_format.return_value = {'datasets': []}
        request = rf.get('/storage-allocation-history/5/')
        request.user = make_user(is_superuser=True)

        response = views.storage_allocation_history(request, allocation_id='5')

        assert response.status_code == 200
        mock_utils.user_has_allocation_access.assert_not_called()

    @patch('tufts_local.views.views.get_object_or_404')
    @patch('tufts_local.views.views.utils')
    def test_error_returns_500(self, mock_utils, mock_get_object, rf):
        mock_utils.user_has_allocation_access.return_value = True
        mock_get_object.side_effect = Exception('allocation missing')
        request = rf.get('/storage-allocation-history/5/')
        request.user = make_user(is_superuser=False)

        response = views.storage_allocation_history(request, allocation_id='5')

        assert response.status_code == 500
        assert json.loads(response.content) == {'message': 'error occurred: allocation missing'}
