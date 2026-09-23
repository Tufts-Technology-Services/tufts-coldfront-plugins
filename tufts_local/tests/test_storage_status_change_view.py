# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from tufts_local.status_change_utils import StatusChangeAPIError
from tufts_local.tests.fakes import DummyStatusChangeAPIClient
from tufts_local.views import storage_status_change_review


def make_user(is_superuser=False):
    user = MagicMock(name='user')
    user.is_authenticated = True
    user.is_superuser = is_superuser
    user.username = 'rdms_admin'
    return user


def make_client(records=None):
    client = MagicMock()
    # fetch_all_pending_reviews() pages until it gets an empty list back
    client.get_pending_reviews.side_effect = [records or [], []]
    return client


def add_session(request):
    # rendering the template requires django_su's context processor, which reads request.session
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()


@pytest.fixture
def rf():
    return RequestFactory()


@pytest.fixture
def dummy_client():
    """A real in-memory client, so the view's pagination and filtering run for real."""
    DummyStatusChangeAPIClient.reset()
    yield DummyStatusChangeAPIClient()
    DummyStatusChangeAPIClient.reset()


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
        mock_get_client.return_value = make_client(records=[{'username': 'jdoe01', 'date': '2026-09-10', 'notes': []}])
        request = rf.get('/storage-status-change-review/')
        request.user = make_user(is_superuser=True)
        add_session(request)

        response = storage_status_change_review(request)
        response.render()

        assert response.status_code == 200
        assert b'jdoe01' in response.content

    @pytest.mark.django_db
    @pytest.mark.urls('tufts_local.tests.urls')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_renders_the_field_names_the_service_returns(self, mock_get_client, rf):
        """Guards the whole template against the API's field names.

        The service's record keys differ from the ones this page was first built against
        (username not utln, ncq_eligible_old not old_ncq_eligibility, 'Yes'/'No' strings
        not booleans), and a template reading a key that isn't there renders empty rather
        than failing -- so assert on values that only appear if each name resolved.
        """
        mock_get_client.return_value = make_client(records=DummyStatusChangeAPIClient._SEED_RECORDS)
        request = rf.get('/storage-status-change-review/')
        request.user = make_user(is_superuser=True)
        add_session(request)

        response = storage_status_change_review(request)
        response.render()
        content = response.content.decode()

        # the record key, as the modal posts it back
        assert '<input type="hidden" name="username" value="jdoe01">' in content
        assert '<input type="hidden" name="record_date" value="2026-09-10">' in content
        # old -> new pairs, and the role/sharer columns that hold 'Yes'/'No' rather than booleans
        assert 'Research Assistant Professor' in content and 'Emeritus' in content
        # jdoe01 and kwong03 but not asmith02, each badged twice: in its row and in its modal
        assert content.count('>Owner</span>') == 4
        # notes come back newest first
        assert 'Grant renewal confirmed by RA office.' in content

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
        request = rf.post(
            '/storage-status-change-review/',
            {'username': 'jdoe01', 'record_date': '2026-09-10', 'action': 'acknowledge', 'notes': 'ok'},
        )
        request.user = make_user(is_superuser=True)

        response = storage_status_change_review(request)

        assert response.status_code == 302
        client.acknowledge.assert_called_once_with('jdoe01', '2026-09-10', reviewer='rdms_admin', note='ok')
        mock_messages.success.assert_called_once()

    @pytest.mark.urls('tufts_local.urls')
    @patch('tufts_local.views.storage_status_change_view.messages')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_blank_note_is_passed_as_none(self, mock_get_client, mock_messages, rf):
        client = make_client()
        mock_get_client.return_value = client
        request = rf.post(
            '/storage-status-change-review/',
            {'username': 'jdoe01', 'record_date': '2026-09-10', 'action': 'acknowledge', 'notes': '   '},
        )
        request.user = make_user(is_superuser=True)

        storage_status_change_review(request)

        client.acknowledge.assert_called_once_with('jdoe01', '2026-09-10', reviewer='rdms_admin', note=None)

    @pytest.mark.urls('tufts_local.urls')
    @patch('tufts_local.views.storage_status_change_view.messages')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_grace_period_success_redirects(self, mock_get_client, mock_messages, rf):
        client = make_client()
        mock_get_client.return_value = client
        request = rf.post(
            '/storage-status-change-review/',
            {
                'username': 'asmith02',
                'record_date': '2026-09-12',
                'action': 'grace-period',
                'ncq_expiration_date': '2026-12-31',
                'notes': '',
            },
        )
        request.user = make_user(is_superuser=True)

        response = storage_status_change_review(request)

        assert response.status_code == 302
        client.grant_grace_period.assert_called_once_with(
            'asmith02', '2026-09-12', reviewer='rdms_admin', expiration_date='2026-12-31', note=None
        )
        mock_messages.success.assert_called_once()

    @pytest.mark.urls('tufts_local.urls')
    @patch('tufts_local.views.storage_status_change_view.messages')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_action_error_shows_message_and_still_redirects(self, mock_get_client, mock_messages, rf):
        client = make_client()
        client.acknowledge.side_effect = StatusChangeAPIError('no such record')
        mock_get_client.return_value = client
        request = rf.post(
            '/storage-status-change-review/',
            {'username': 'nobody', 'record_date': '2026-09-10', 'action': 'acknowledge', 'notes': ''},
        )
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
        request = rf.post(
            '/storage-status-change-review/', {'username': 'jdoe01', 'record_date': '2026-09-10', 'action': 'bogus'}
        )
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
            '/storage-status-change-review/',
            {'username': 'jdoe01', 'record_date': '2026-09-10', 'action': 'note', 'notes': 'checked with PI'},
        )
        request.user = make_user(is_superuser=True)

        response = storage_status_change_review(request)

        assert response.status_code == 302
        client.add_note.assert_called_once_with('jdoe01', '2026-09-10', 'checked with PI', reviewer='rdms_admin')
        client.acknowledge.assert_not_called()
        client.grant_grace_period.assert_not_called()
        mock_messages.success.assert_called_once()

    @pytest.mark.urls('tufts_local.urls')
    @patch('tufts_local.views.storage_status_change_view.messages')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_save_note_without_text_shows_error(self, mock_get_client, mock_messages, rf):
        client = make_client()
        mock_get_client.return_value = client
        request = rf.post(
            '/storage-status-change-review/',
            {'username': 'jdoe01', 'record_date': '2026-09-10', 'action': 'note', 'notes': '   '},
        )
        request.user = make_user(is_superuser=True)

        response = storage_status_change_review(request)

        assert response.status_code == 302
        client.add_note.assert_not_called()
        mock_messages.error.assert_called_once()
        mock_messages.success.assert_not_called()


class TestShowAcknowledgedToggle:
    @pytest.mark.django_db
    @pytest.mark.urls('tufts_local.tests.urls')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_defaults_to_unreviewed_records_only(self, mock_get_client, rf):
        """The page is a work queue, so it opens on what is still outstanding."""
        client = make_client()
        mock_get_client.return_value = client
        request = rf.get('/storage-status-change-review/')
        request.user = make_user(is_superuser=True)
        add_session(request)

        response = storage_status_change_review(request)

        assert response.context_data['show_acknowledged'] is False
        assert client.get_pending_reviews.call_args.kwargs['include_acknowledged'] is False

    @pytest.mark.django_db
    @pytest.mark.urls('tufts_local.tests.urls')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_param_asks_the_client_for_reviewed_records_too(self, mock_get_client, rf):
        client = make_client()
        mock_get_client.return_value = client
        request = rf.get('/storage-status-change-review/', {'show_acknowledged': '1'})
        request.user = make_user(is_superuser=True)
        add_session(request)

        response = storage_status_change_review(request)

        assert response.context_data['show_acknowledged'] is True
        assert client.get_pending_reviews.call_args.kwargs['include_acknowledged'] is True

    @pytest.mark.django_db
    @pytest.mark.urls('tufts_local.tests.urls')
    @pytest.mark.parametrize('value', ['0', 'false', 'off', '', 'maybe'])
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_only_a_truthy_value_turns_it_on(self, mock_get_client, rf, value):
        """Anything but an explicit yes falls back to the safe default rather than
        being read as 'the param is present, so show everything'."""
        mock_get_client.return_value = make_client()
        request = rf.get('/storage-status-change-review/', {'show_acknowledged': value})
        request.user = make_user(is_superuser=True)
        add_session(request)

        assert storage_status_change_review(request).context_data['show_acknowledged'] is False

    @pytest.mark.django_db
    @pytest.mark.urls('tufts_local.tests.urls')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_acknowledged_record_is_hidden_then_shown(self, mock_get_client, rf, dummy_client):
        mock_get_client.return_value = dummy_client
        dummy_client.acknowledge('jdoe01', '2026-09-10', reviewer='rdms_admin')

        def usernames(query):
            request = rf.get('/storage-status-change-review/', query)
            request.user = make_user(is_superuser=True)
            add_session(request)
            return {r['username'] for r in storage_status_change_review(request).context_data['records']}

        assert 'jdoe01' not in usernames({})
        assert 'jdoe01' in usernames({'show_acknowledged': '1'})

    @pytest.mark.django_db
    @pytest.mark.urls('tufts_local.tests.urls')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_default_view_offers_the_toggle_and_omits_the_review_column(self, mock_get_client, rf, dummy_client):
        mock_get_client.return_value = dummy_client
        request = rf.get('/storage-status-change-review/')
        request.user = make_user(is_superuser=True)
        add_session(request)

        response = storage_status_change_review(request)
        response.render()
        content = response.content.decode()

        assert 'show_acknowledged=1' in content
        assert 'Show acknowledged' in content
        assert 'Hide acknowledged' not in content
        # the column would say Pending on every row here, so it stays out of the way
        assert '<th scope="col">Review</th>' not in content

    @pytest.mark.django_db
    @pytest.mark.urls('tufts_local.tests.urls')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_showing_acknowledged_badges_the_reviewed_rows(self, mock_get_client, rf, dummy_client):
        mock_get_client.return_value = dummy_client
        dummy_client.acknowledge('jdoe01', '2026-09-10', reviewer='rdms_admin')
        request = rf.get('/storage-status-change-review/', {'show_acknowledged': '1'})
        request.user = make_user(is_superuser=True)
        add_session(request)

        response = storage_status_change_review(request)
        response.render()
        content = response.content.decode()

        assert 'Hide acknowledged' in content
        assert 'Show acknowledged' not in content
        assert '<th scope="col">Review</th>' in content
        # one acknowledged record among the three, the rest still pending
        assert content.count('>Acknowledged</span>') == 1
        assert content.count('>Pending</span>') == 2
        # the toggle rides along on each row's form so acting on one doesn't reset the view
        assert content.count('<input type="hidden" name="show_acknowledged" value="1">') == 3

    @pytest.mark.urls('tufts_local.urls')
    @patch('tufts_local.views.storage_status_change_view.messages')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_post_keeps_the_toggle_on_the_redirect(self, mock_get_client, mock_messages, rf):
        """Acknowledging while showing everything must not bounce the reviewer back to the
        pending-only view, where the row they just acted on has disappeared."""
        mock_get_client.return_value = make_client()
        request = rf.post(
            '/storage-status-change-review/',
            {
                'username': 'jdoe01',
                'record_date': '2026-09-10',
                'action': 'acknowledge',
                'show_acknowledged': '1',
            },
        )
        request.user = make_user(is_superuser=True)

        response = storage_status_change_review(request)

        assert response.status_code == 302
        assert response.url.endswith('?show_acknowledged=1')

    @pytest.mark.urls('tufts_local.urls')
    @patch('tufts_local.views.storage_status_change_view.messages')
    @patch('tufts_local.views.storage_status_change_view.get_status_change_client')
    def test_post_without_the_toggle_redirects_to_the_default_view(self, mock_get_client, mock_messages, rf):
        mock_get_client.return_value = make_client()
        request = rf.post(
            '/storage-status-change-review/',
            {'username': 'jdoe01', 'record_date': '2026-09-10', 'action': 'acknowledge'},
        )
        request.user = make_user(is_superuser=True)

        response = storage_status_change_review(request)

        assert response.status_code == 302
        assert 'show_acknowledged' not in response.url
