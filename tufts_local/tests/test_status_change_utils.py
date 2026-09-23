# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import copy
from unittest.mock import MagicMock, patch

import pytest
import requests

from tufts_local.status_change_utils import (
    ACTIVE_STATUS_VALUES,
    StatusChangeAPIClient,
    StatusChangeAPIError,
    fetch_all_pending_reviews,
    get_status_change_client,
)
from tufts_local.tests.fakes import DummyStatusChangeAPIClient

JDOE = ('jdoe01', '2026-09-10')
ASMITH = ('asmith02', '2026-09-12')
KWONG = ('kwong03', '2026-09-14')


@pytest.fixture(autouse=True)
def reset_dummy_records():
    original = copy.deepcopy(DummyStatusChangeAPIClient._records)
    yield
    DummyStatusChangeAPIClient._records = copy.deepcopy(original)


@pytest.fixture
def client():
    return DummyStatusChangeAPIClient()


@pytest.fixture
def api_client():
    return StatusChangeAPIClient(base_url='https://analytics.example/api/v1/secure', token='secret')


def make_response(status_code=200, json_data=None, text=''):
    response = MagicMock(name='response')
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.text = text
    response.json.return_value = json_data if json_data is not None else {}
    return response


class TestGetStatusChangeClient:
    def test_reads_its_configuration_from_the_environment(self, monkeypatch):
        monkeypatch.setenv('RT_ANALYTICS_BASE_URL', 'https://analytics.example/api/v1/secure')
        monkeypatch.setenv('RT_ANALYTICS_API_KEY', 'secret')

        client = get_status_change_client()

        assert client.base_url == 'https://analytics.example/api/v1/secure'
        assert client.token == 'secret'

    def test_never_hands_back_a_test_double(self, monkeypatch):
        """The stand-in lives in the test package precisely so no configuration can
        put invented records in front of someone who thinks they are live."""
        monkeypatch.setenv('RT_ANALYTICS_BASE_URL', 'https://analytics.example/api/v1/secure')

        assert type(get_status_change_client()) is StatusChangeAPIClient

    def test_trailing_slash_on_base_url_does_not_double_up(self, monkeypatch):
        monkeypatch.setenv('RT_ANALYTICS_BASE_URL', 'https://analytics.example/api/v1/secure/')

        assert get_status_change_client().base_url == 'https://analytics.example/api/v1/secure'


class TestAPIClientGetPendingReviews:
    @patch('tufts_local.status_change_utils.requests.request')
    def test_asks_only_for_unreviewed_records(self, mock_request, api_client):
        mock_request.return_value = make_response(json_data={'total_count': 1, 'results': [{'username': 'jdoe01'}]})

        records = api_client.get_pending_reviews()

        assert records == [{'username': 'jdoe01'}]
        method, url = mock_request.call_args.args
        assert method == 'GET'
        assert url == 'https://analytics.example/api/v1/secure/storage-owner-status-change'
        assert mock_request.call_args.kwargs['params']['reviewed_by_rdms'] == 'No'
        assert mock_request.call_args.kwargs['headers'] == {'X-API-Key': 'secret'}

    @patch('tufts_local.status_change_utils.requests.request')
    def test_include_acknowledged_drops_the_filter(self, mock_request, api_client):
        mock_request.return_value = make_response(json_data={'total_count': 0, 'results': []})

        api_client.get_pending_reviews(include_acknowledged=True)

        assert 'reviewed_by_rdms' not in mock_request.call_args.kwargs['params']

    @patch('tufts_local.status_change_utils.requests.request')
    def test_rows_is_capped_at_the_services_limit(self, mock_request, api_client):
        mock_request.return_value = make_response(json_data={'total_count': 0, 'results': []})

        api_client.get_pending_reviews(rows=10_000)

        assert mock_request.call_args.kwargs['params']['rows'] == 500

    @patch('tufts_local.status_change_utils.requests.request')
    def test_404_is_an_empty_page_not_an_error(self, mock_request, api_client):
        """The service 404s rather than returning an empty result set, so an empty
        review queue must not surface to the user as a failure."""
        mock_request.return_value = make_response(status_code=404)

        assert api_client.get_pending_reviews() == []

    @patch('tufts_local.status_change_utils.requests.request')
    def test_server_error_raises(self, mock_request, api_client):
        mock_request.return_value = make_response(status_code=500, text='boom')

        with pytest.raises(StatusChangeAPIError, match='500'):
            api_client.get_pending_reviews()

    @patch('tufts_local.status_change_utils.requests.request')
    def test_unauthorized_raises(self, mock_request, api_client):
        mock_request.return_value = make_response(status_code=401, text='Invalid API key')

        with pytest.raises(StatusChangeAPIError, match='401'):
            api_client.get_pending_reviews()

    @patch('tufts_local.status_change_utils.requests.request')
    def test_connection_failure_raises(self, mock_request, api_client):
        mock_request.side_effect = requests.ConnectionError('no route to host')

        with pytest.raises(StatusChangeAPIError, match='Could not reach'):
            api_client.get_pending_reviews()

    @patch('tufts_local.status_change_utils.requests.request')
    def test_non_json_body_raises(self, mock_request, api_client):
        response = make_response()
        response.json.side_effect = ValueError('not json')
        mock_request.return_value = response

        with pytest.raises(StatusChangeAPIError, match='non-JSON'):
            api_client.get_pending_reviews()

    @patch('tufts_local.status_change_utils.requests.request')
    def test_unconfigured_base_url_raises_before_any_request(self, mock_request):
        with pytest.raises(StatusChangeAPIError, match='RT_ANALYTICS_BASE_URL'):
            StatusChangeAPIClient().get_pending_reviews()

        mock_request.assert_not_called()


class TestAPIClientWrites:
    @patch('tufts_local.status_change_utils.requests.request')
    def test_acknowledge_patches_the_record(self, mock_request, api_client):
        mock_request.return_value = make_response(json_data={'change': {}, 'notes': []})

        api_client.acknowledge('jdoe01', '2026-09-10', reviewer='rdms_admin', note='looks fine')

        method, url = mock_request.call_args.args
        assert method == 'PATCH'
        assert url == 'https://analytics.example/api/v1/secure/storage-owner-status-change/jdoe01/2026-09-10'
        assert mock_request.call_args.kwargs['json'] == {
            'author_utln': 'rdms_admin',
            'reviewed_by_rdms': 'Yes',
            'note': 'looks fine',
        }

    @patch('tufts_local.status_change_utils.requests.request')
    def test_omitted_note_is_left_out_of_the_body(self, mock_request, api_client):
        """The service treats a null note as a field to write; it has to be absent, not None."""
        mock_request.return_value = make_response(json_data={'change': {}, 'notes': []})

        api_client.acknowledge('jdoe01', '2026-09-10', reviewer='rdms_admin')

        assert mock_request.call_args.kwargs['json'] == {'author_utln': 'rdms_admin', 'reviewed_by_rdms': 'Yes'}

    @patch('tufts_local.status_change_utils.requests.request')
    def test_grace_period_sends_the_expiration_date(self, mock_request, api_client):
        mock_request.return_value = make_response(json_data={'change': {}, 'notes': []})

        api_client.grant_grace_period('asmith02', '2026-09-12', reviewer='rdms_admin', expiration_date='2026-12-31')

        assert mock_request.call_args.kwargs['json'] == {
            'author_utln': 'rdms_admin',
            'ncq_expiration_date': '2026-12-31',
        }

    @patch('tufts_local.status_change_utils.requests.request')
    def test_grace_period_without_a_date_raises_before_any_request(self, mock_request, api_client):
        """Without a date the service would just mark the record reviewed, silently doing
        something other than what the reviewer asked for."""
        with pytest.raises(StatusChangeAPIError, match='expiration date'):
            api_client.grant_grace_period('asmith02', '2026-09-12', reviewer='rdms_admin', expiration_date='')

        mock_request.assert_not_called()

    @patch('tufts_local.status_change_utils.requests.request')
    def test_add_note_sends_only_the_note(self, mock_request, api_client):
        mock_request.return_value = make_response(json_data={'change': {}, 'notes': []})

        api_client.add_note('kwong03', '2026-09-14', 'checked with PI', reviewer='rdms_admin')

        assert mock_request.call_args.kwargs['json'] == {'author_utln': 'rdms_admin', 'note': 'checked with PI'}

    @patch('tufts_local.status_change_utils.requests.request')
    def test_empty_note_raises_before_any_request(self, mock_request, api_client):
        with pytest.raises(StatusChangeAPIError, match='Note text'):
            api_client.add_note('kwong03', '2026-09-14', '', reviewer='rdms_admin')

        mock_request.assert_not_called()

    @patch('tufts_local.status_change_utils.requests.request')
    def test_404_on_a_write_is_a_missing_record(self, mock_request, api_client):
        """Unlike a listing, a 404 here means the record really is gone, so it must raise."""
        mock_request.return_value = make_response(status_code=404)

        with pytest.raises(StatusChangeAPIError, match='No status change record found'):
            api_client.acknowledge('nobody', '2026-09-10', reviewer='rdms_admin')


class TestFetchAllPendingReviews:
    def test_defaults_to_unreviewed_records_only(self):
        client = MagicMock()
        client.get_pending_reviews.return_value = []

        fetch_all_pending_reviews(client)

        assert client.get_pending_reviews.call_args.kwargs['include_acknowledged'] is False

    def test_passes_include_acknowledged_through(self):
        client = MagicMock()
        client.get_pending_reviews.return_value = []

        fetch_all_pending_reviews(client, include_acknowledged=True)

        assert client.get_pending_reviews.call_args.kwargs['include_acknowledged'] is True

    def test_acknowledged_records_are_excluded_by_default(self, client):
        client.acknowledge(*JDOE, reviewer='rdms_admin')

        usernames = {r['username'] for r in fetch_all_pending_reviews(client)}

        assert JDOE[0] not in usernames

    def test_acknowledged_records_come_back_when_asked_for(self, client):
        client.acknowledge(*JDOE, reviewer='rdms_admin')

        usernames = {r['username'] for r in fetch_all_pending_reviews(client, include_acknowledged=True)}

        assert JDOE[0] in usernames

    def test_walks_every_page(self):
        client = MagicMock()
        client.get_pending_reviews.side_effect = [[{'username': 'a'}], [{'username': 'b'}], []]

        records = fetch_all_pending_reviews(client)

        assert records == [{'username': 'a'}, {'username': 'b'}]
        assert [call.kwargs['start'] for call in client.get_pending_reviews.call_args_list] == [0, 1, 2]

    def test_empty_queue_makes_one_call(self):
        client = MagicMock()
        client.get_pending_reviews.return_value = []

        assert fetch_all_pending_reviews(client) == []
        assert client.get_pending_reviews.call_count == 1

    def test_returns_everything_the_dummy_has(self, client):
        assert len(fetch_all_pending_reviews(client)) == len(DummyStatusChangeAPIClient._SEED_RECORDS)


class TestDummySeedData:
    """The seed records stand in for real ones, so they have to be able to occur.

    Nothing validates the remote column -- it is a bare CHAR(1) -- so a demo value the
    service could never send would have the page looking right against data that lies.
    """

    @pytest.mark.parametrize('field', ['active_status_old', 'active_status_new'])
    def test_active_status_uses_a_real_code(self, field):
        values = {record[field] for record in DummyStatusChangeAPIClient._SEED_RECORDS}

        assert values <= set(ACTIVE_STATUS_VALUES)

    @pytest.mark.parametrize(
        'field',
        [
            'current_project_owner',
            'current_project_approver',
            'current_ncq_sharer',
            'ncq_eligible_old',
            'ncq_eligible_new',
            'reviewed_by_rdms',
        ],
    )
    def test_yes_no_columns_hold_strings_not_booleans(self, field):
        values = {record[field] for record in DummyStatusChangeAPIClient._SEED_RECORDS}

        assert values <= {'Yes', 'No'}


class TestDummyGetPendingReviews:
    def test_only_returns_unreviewed_records(self, client):
        records = client.get_pending_reviews()

        assert len(records) > 0
        assert all(r['reviewed_by_rdms'] == 'No' for r in records)

    def test_reviewed_records_are_excluded(self, client):
        client.acknowledge(*JDOE, reviewer='rdms_admin')

        records = client.get_pending_reviews()

        assert JDOE[0] not in {r['username'] for r in records}

    def test_include_acknowledged_keeps_reviewed_records(self, client):
        client.acknowledge(*JDOE, reviewer='rdms_admin')

        records = client.get_pending_reviews(include_acknowledged=True)

        assert JDOE[0] in {r['username'] for r in records}

    def test_start_and_rows_paginate(self, client):
        first, second = client.get_pending_reviews(rows=1), client.get_pending_reviews(start=1, rows=1)

        assert len(first) == len(second) == 1
        assert first[0]['username'] != second[0]['username']

    def test_start_past_the_end_is_empty(self, client):
        assert client.get_pending_reviews(start=99) == []


class TestDummyAcknowledge:
    def test_marks_reviewed_and_sets_review_date(self, client):
        client.acknowledge(*JDOE, reviewer='rdms_admin')

        record = client._get_record(*JDOE)
        assert record['reviewed_by_rdms'] == 'Yes'
        assert record['review_date'] is not None

    def test_adds_the_services_automatic_note(self, client):
        client.acknowledge(*JDOE, reviewer='rdms_admin')

        note = client._get_record(*JDOE)['notes'][0]
        assert note['note'] == 'Reviewed'
        assert note['author_utln'] == 'rdms_admin'

    def test_reviewer_note_is_added_on_top_of_the_automatic_one(self, client):
        notes_before = len(client._get_record(*JDOE)['notes'])

        client.acknowledge(*JDOE, reviewer='rdms_admin', note='looks fine')

        notes = client._get_record(*JDOE)['notes']
        assert len(notes) == notes_before + 2
        assert notes[0]['note'] == 'looks fine'
        assert 'note_timestamp' in notes[0]

    def test_unknown_record_raises(self, client):
        with pytest.raises(StatusChangeAPIError):
            client.acknowledge('nobody', '2026-01-01', reviewer='rdms_admin')

    def test_known_user_on_the_wrong_date_raises(self, client):
        """(username, date) is the key; matching on the username alone would touch the
        wrong row for a user with more than one change."""
        with pytest.raises(StatusChangeAPIError):
            client.acknowledge('jdoe01', '2026-01-01', reviewer='rdms_admin')


class TestDummyGrantGracePeriod:
    def test_sets_expiration_and_marks_reviewed(self, client):
        client.grant_grace_period(*ASMITH, reviewer='rdms_admin', expiration_date='2026-12-31')

        record = client._get_record(*ASMITH)
        assert record['reviewed_by_rdms'] == 'Yes'
        assert record['ncq_expiration_date'] == '2026-12-31'

    def test_adds_the_services_automatic_note(self, client):
        client.grant_grace_period(*ASMITH, reviewer='rdms_admin', expiration_date='2026-12-31')

        assert client._get_record(*ASMITH)['notes'][-1]['note'] == 'Grace period granted until 2026-12-31'

    def test_reviewer_note_is_added_on_top_of_the_automatic_one(self, client):
        client.grant_grace_period(
            *ASMITH, reviewer='rdms_admin', expiration_date='2026-12-31', note='extended per PI request'
        )

        notes = client._get_record(*ASMITH)['notes']
        assert notes[0]['note'] == 'extended per PI request'
        assert notes[0]['author_utln'] == 'rdms_admin'

    def test_unknown_record_raises(self, client):
        with pytest.raises(StatusChangeAPIError):
            client.grant_grace_period('nobody', '2026-01-01', reviewer='rdms_admin', expiration_date='2026-12-31')


class TestDummyAddNote:
    def test_prepends_to_existing_notes(self, client):
        notes_before = len(client._get_record(*KWONG)['notes'])

        client.add_note(*KWONG, 'additional context', reviewer='rdms_admin')

        notes = client._get_record(*KWONG)['notes']
        assert len(notes) == notes_before + 1
        assert notes[0]['note'] == 'additional context'
        assert notes[0]['author_utln'] == 'rdms_admin'

    def test_does_not_mark_the_record_reviewed(self, client):
        client.add_note(*KWONG, 'additional context', reviewer='rdms_admin')

        assert client._get_record(*KWONG)['reviewed_by_rdms'] == 'No'

    def test_unknown_record_raises(self, client):
        with pytest.raises(StatusChangeAPIError):
            client.add_note('nobody', '2026-01-01', 'note', reviewer='rdms_admin')


class TestDummyReset:
    def test_restores_seed_data_after_mutation(self, client):
        seed_snapshot = copy.deepcopy(DummyStatusChangeAPIClient._SEED_RECORDS)

        client.acknowledge(*JDOE, reviewer='rdms_admin', note='mutated')
        client.grant_grace_period(*ASMITH, reviewer='rdms_admin', expiration_date='2026-12-31')

        DummyStatusChangeAPIClient.reset()

        assert DummyStatusChangeAPIClient._records == seed_snapshot

    def test_reset_does_not_mutate_seed_data(self, client):
        seed_snapshot = copy.deepcopy(DummyStatusChangeAPIClient._SEED_RECORDS)

        DummyStatusChangeAPIClient.reset()
        client.acknowledge(*JDOE, reviewer='rdms_admin')

        assert DummyStatusChangeAPIClient._SEED_RECORDS == seed_snapshot
