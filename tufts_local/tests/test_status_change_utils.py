import copy

import pytest

from tufts_local.status_change_utils import (
    DummyStatusChangeAPIClient,
    StatusChangeAPIClient,
    StatusChangeAPIError,
    get_status_change_client,
)


@pytest.fixture(autouse=True)
def reset_dummy_records():
    original = copy.deepcopy(DummyStatusChangeAPIClient._records)
    yield
    DummyStatusChangeAPIClient._records = copy.deepcopy(original)


@pytest.fixture
def client():
    return DummyStatusChangeAPIClient()


def test_get_status_change_client_returns_dummy_client():
    assert isinstance(get_status_change_client(), DummyStatusChangeAPIClient)


class TestStatusChangeAPIClientStub:
    def test_methods_are_not_implemented(self):
        stub = StatusChangeAPIClient()

        with pytest.raises(NotImplementedError):
            stub.get_pending_reviews()
        with pytest.raises(NotImplementedError):
            stub.acknowledge(1, reviewer='rdms_admin')
        with pytest.raises(NotImplementedError):
            stub.grant_grace_period(1, reviewer='rdms_admin', expiration_date='2026-01-01')
        with pytest.raises(NotImplementedError):
            stub.add_note(1, 'note')


class TestGetPendingReviews:
    def test_only_returns_unreviewed_records(self, client):
        records = client.get_pending_reviews()

        assert len(records) > 0
        assert all(not r['reviewed_by_rdms'] for r in records)

    def test_reviewed_records_are_excluded(self, client):
        client.acknowledge(1, reviewer='rdms_admin')

        records = client.get_pending_reviews()

        assert 1 not in {r['id'] for r in records}


class TestAcknowledge:
    def test_marks_reviewed_and_sets_review_date(self, client):
        client.acknowledge(1, reviewer='rdms_admin')

        record = client._get_record(1)
        assert record['reviewed_by_rdms'] is True
        assert record['review_date'] is not None

    def test_appends_note_when_given(self, client):
        client.acknowledge(1, reviewer='rdms_admin', note='looks fine')

        record = client._get_record(1)
        assert record['notes'][-1]['note'] == 'looks fine'
        assert 'timestamp' in record['notes'][-1]

    def test_no_note_added_when_note_is_none(self, client):
        notes_before = len(client._get_record(2)['notes'])

        client.acknowledge(2, reviewer='rdms_admin')

        assert len(client._get_record(2)['notes']) == notes_before

    def test_unknown_record_raises(self, client):
        with pytest.raises(StatusChangeAPIError):
            client.acknowledge(999, reviewer='rdms_admin')


class TestGrantGracePeriod:
    def test_sets_expiration_and_marks_reviewed(self, client):
        client.grant_grace_period(2, reviewer='rdms_admin', expiration_date='2026-12-31')

        record = client._get_record(2)
        assert record['reviewed_by_rdms'] is True
        assert record['ncq_expiration_date'] == '2026-12-31'

    def test_appends_note_when_given(self, client):
        client.grant_grace_period(
            2, reviewer='rdms_admin', expiration_date='2026-12-31', note='extended per PI request'
        )

        record = client._get_record(2)
        assert record['notes'][-1]['note'] == 'extended per PI request'

    def test_unknown_record_raises(self, client):
        with pytest.raises(StatusChangeAPIError):
            client.grant_grace_period(999, reviewer='rdms_admin', expiration_date='2026-12-31')


class TestAddNote:
    def test_appends_to_existing_notes(self, client):
        notes_before = len(client._get_record(3)['notes'])

        client.add_note(3, 'additional context')

        record = client._get_record(3)
        assert len(record['notes']) == notes_before + 1
        assert record['notes'][-1]['note'] == 'additional context'

    def test_unknown_record_raises(self, client):
        with pytest.raises(StatusChangeAPIError):
            client.add_note(999, 'note')
