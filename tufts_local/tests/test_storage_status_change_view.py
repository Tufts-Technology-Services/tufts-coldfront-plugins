# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from tufts_local.status_change_utils import StatusChangeAPIError
from tufts_local.views import storage_status_change_reset_demo_data, storage_status_change_review


def make_user(is_superuser=False):
    user = MagicMock(name='user')
    user.is_authenticated = True
    user.is_superuser = is_superuser
    user.username = 'rdms_admin'
    return user


def make_client(records=None):
    client = MagicMock()
    client.get_pending_reviews.return_value = records if records is not None else []
    return client


def add_session(request):
    # rendering the template requires django_su's context processor, which reads request.session
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()


@pytest.fixture
def rf():
    return RequestFactory()


class TestStorageStatusChangeReviewAccess:
    def test_anonymous_user_redirects_to_login(self, rf):
        request = rf.get('/storage-status-change-review/')
        request.user = AnonymousUser()

        response = storage_status_change_review(request)

        assert response.status_code == 302
        assert 'login' in response.url

    def test_non_superuser_redirects_to_login(self, rf):
        request = rf.get('/storage-status-change-review/')
        request.user = make_user(is_superuser=False)

        response = storage_status_change_review(request)

        assert response.status_code == 302
        assert 'login' in response.url

    @pytest.mark.django_db
    @pytest.mark.urls('tufts_local.tests.urls')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_superuser_sees_pending_records(self, mock_get_client, rf):
        mock_get_client.return_value = make_client(records=[{'id': 1, 'utln': 'jdoe01', 'notes': []}])
        request = rf.get('/storage-status-change-review/')
        request.user = make_user(is_superuser=True)
        add_session(request)

        response = storage_status_change_review(request)
        response.render()

        assert response.status_code == 200
        assert b'jdoe01' in response.content

    @pytest.mark.django_db
    @pytest.mark.urls('tufts_local.tests.urls')
    @patch('tufts_local.views.storage_status_change_view.messages')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_fetch_error_shows_message_and_renders_empty(self, mock_get_client, mock_messages, rf):
        client = make_client()
        client.get_pending_reviews.side_effect = StatusChangeAPIError('unreachable')
        mock_get_client.return_value = client
        request = rf.get('/storage-status-change-review/')
        request.user = make_user(is_superuser=True)
        add_session(request)

        response = storage_status_change_review(request)
        response.render()

        assert response.status_code == 200
        assert response.context_data['records'] == []
        mock_messages.error.assert_called_once()


class TestStorageStatusChangeReviewSubmit:
    @pytest.mark.urls('tufts_local.urls')
    @patch('tufts_local.views.storage_status_change_view.messages')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_acknowledge_success_redirects(self, mock_get_client, mock_messages, rf):
        client = make_client()
        mock_get_client.return_value = client
        request = rf.post('/storage-status-change-review/', {'record_id': '1', 'action': 'acknowledge', 'notes': 'ok'})
        request.user = make_user(is_superuser=True)

        response = storage_status_change_review(request)

        assert response.status_code == 302
        client.acknowledge.assert_called_once_with('1', reviewer='rdms_admin', note='ok')
        mock_messages.success.assert_called_once()

    @pytest.mark.urls('tufts_local.urls')
    @patch('tufts_local.views.storage_status_change_view.messages')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_blank_note_is_passed_as_none(self, mock_get_client, mock_messages, rf):
        client = make_client()
        mock_get_client.return_value = client
        request = rf.post('/storage-status-change-review/', {'record_id': '1', 'action': 'acknowledge', 'notes': '   '})
        request.user = make_user(is_superuser=True)

        storage_status_change_review(request)

        client.acknowledge.assert_called_once_with('1', reviewer='rdms_admin', note=None)

    @pytest.mark.urls('tufts_local.urls')
    @patch('tufts_local.views.storage_status_change_view.messages')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_grace_period_success_redirects(self, mock_get_client, mock_messages, rf):
        client = make_client()
        mock_get_client.return_value = client
        request = rf.post(
            '/storage-status-change-review/',
            {'record_id': '2', 'action': 'grace-period', 'ncq_expiration_date': '2026-12-31', 'notes': ''},
        )
        request.user = make_user(is_superuser=True)

        response = storage_status_change_review(request)

        assert response.status_code == 302
        client.grant_grace_period.assert_called_once_with(
            '2', reviewer='rdms_admin', expiration_date='2026-12-31', note=None
        )
        mock_messages.success.assert_called_once()

    @pytest.mark.urls('tufts_local.urls')
    @patch('tufts_local.views.storage_status_change_view.messages')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_action_error_shows_message_and_still_redirects(self, mock_get_client, mock_messages, rf):
        client = make_client()
        client.acknowledge.side_effect = StatusChangeAPIError('no such record')
        mock_get_client.return_value = client
        request = rf.post('/storage-status-change-review/', {'record_id': '99', 'action': 'acknowledge', 'notes': ''})
        request.user = make_user(is_superuser=True)

        response = storage_status_change_review(request)

        assert response.status_code == 302
        mock_messages.error.assert_called_once()
        mock_messages.success.assert_not_called()

    @pytest.mark.urls('tufts_local.urls')
    @patch('tufts_local.views.storage_status_change_view.messages')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_unknown_action_is_noop(self, mock_get_client, mock_messages, rf):
        client = make_client()
        mock_get_client.return_value = client
        request = rf.post('/storage-status-change-review/', {'record_id': '1', 'action': 'bogus'})
        request.user = make_user(is_superuser=True)

        response = storage_status_change_review(request)

        assert response.status_code == 302
        client.acknowledge.assert_not_called()
        client.grant_grace_period.assert_not_called()
        mock_messages.success.assert_not_called()
        mock_messages.error.assert_not_called()

    @pytest.mark.urls('tufts_local.urls')
    @patch('tufts_local.views.storage_status_change_view.messages')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_save_note_success_redirects(self, mock_get_client, mock_messages, rf):
        client = make_client()
        mock_get_client.return_value = client
        request = rf.post(
            '/storage-status-change-review/', {'record_id': '1', 'action': 'note', 'notes': 'checked with PI'}
        )
        request.user = make_user(is_superuser=True)

        response = storage_status_change_review(request)

        assert response.status_code == 302
        client.add_note.assert_called_once_with('1', 'checked with PI', user='rdms_admin')
        client.acknowledge.assert_not_called()
        client.grant_grace_period.assert_not_called()
        mock_messages.success.assert_called_once()

    @pytest.mark.urls('tufts_local.urls')
    @patch('tufts_local.views.storage_status_change_view.messages')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_save_note_without_text_shows_error(self, mock_get_client, mock_messages, rf):
        client = make_client()
        mock_get_client.return_value = client
        request = rf.post('/storage-status-change-review/', {'record_id': '1', 'action': 'note', 'notes': '   '})
        request.user = make_user(is_superuser=True)

        response = storage_status_change_review(request)

        assert response.status_code == 302
        client.add_note.assert_not_called()
        mock_messages.error.assert_called_once()
        mock_messages.success.assert_not_called()


class TestStorageStatusChangeResetDemoData:
    def test_anonymous_user_redirects_to_login(self, rf):
        request = rf.post('/storage-status-change-review/reset/')
        request.user = AnonymousUser()

        response = storage_status_change_reset_demo_data(request)

        assert response.status_code == 302
        assert 'login' in response.url

    def test_non_superuser_redirects_to_login(self, rf):
        request = rf.post('/storage-status-change-review/reset/')
        request.user = make_user(is_superuser=False)

        response = storage_status_change_reset_demo_data(request)

        assert response.status_code == 302
        assert 'login' in response.url

    def test_get_not_allowed(self, rf):
        request = rf.get('/storage-status-change-review/reset/')
        request.user = make_user(is_superuser=True)

        response = storage_status_change_reset_demo_data(request)

        assert response.status_code == 405

    @pytest.mark.urls('tufts_local.urls')
    @patch('tufts_local.views.storage_status_change_view.messages')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_reset_calls_client_reset_and_redirects(self, mock_get_client, mock_messages, rf):
        client = make_client()
        mock_get_client.return_value = client
        request = rf.post('/storage-status-change-review/reset/')
        request.user = make_user(is_superuser=True)

        response = storage_status_change_reset_demo_data(request)

        assert response.status_code == 302
        client.reset.assert_called_once()
        mock_messages.success.assert_called_once()
        mock_messages.error.assert_not_called()

    @pytest.mark.urls('tufts_local.urls')
    @patch('tufts_local.views.storage_status_change_view.messages')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_client_without_reset_shows_error(self, mock_get_client, mock_messages, rf):
        class ClientWithoutReset:
            def get_pending_reviews(self):
                return []

        mock_get_client.return_value = ClientWithoutReset()
        request = rf.post('/storage-status-change-review/reset/')
        request.user = make_user(is_superuser=True)

        response = storage_status_change_reset_demo_data(request)

        assert response.status_code == 302
        mock_messages.error.assert_called_once()
        mock_messages.success.assert_not_called()
