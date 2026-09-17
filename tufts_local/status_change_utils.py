import copy
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class StatusChangeAPIError(Exception):
    pass


class StatusChangeAPIClient:
    """
    Client for the remote storage_user_status_change data source.

    TODO: back this with real HTTP calls once the remote API is available, e.g.
      requests.get(f'{self.base_url}/status-changes/', headers=self._auth_headers, timeout=10)
    """

    def __init__(self, base_url=None, token=None):
        self.base_url = base_url
        self.token = token

    def get_pending_reviews(self):
        raise NotImplementedError

    def acknowledge(self, record_id, reviewer, note=None):
        raise NotImplementedError

    def grant_grace_period(self, record_id, reviewer, expiration_date, note=None):
        raise NotImplementedError

    def add_note(self, record_id, note):
        raise NotImplementedError


class DummyStatusChangeAPIClient(StatusChangeAPIClient):
    """
    In-memory stand-in for the real remote API, used until the storage_user_status_change
    service is available. Mirrors the interface StatusChangeAPIClient will expose so it can
    be swapped out later without changing callers.
    """

    _SEED_RECORDS = [
        {
            'id': 1,
            'date': '2026-09-10',
            'utln': 'jdoe01',
            'current_project_owner': True,
            'current_project_approver': False,
            'share_ncq': True,
            'old_ncq_eligibility': 'Yes',
            'new_ncq_eligibility': 'No',
            'old_active_status': 'Active',
            'new_active_status': 'Active',
            'old_title': 'Research Assistant Professor',
            'new_title': 'Alumni',
            'old_primary_affiliation': 'faculty',
            'new_primary_affiliation': 'alumni',
            'reviewed_by_rdms': False,
            'review_date': None,
            'ncq_expiration_date': None,
            'notes': [
                {'timestamp': '2026-09-10T09:15:00', 'note': 'Flagged by nightly AD sync job.'},
            ],
        },
        {
            'id': 2,
            'date': '2026-09-12',
            'utln': 'asmith02',
            'current_project_owner': False,
            'current_project_approver': True,
            'share_ncq': True,
            'old_ncq_eligibility': 'Yes',
            'new_ncq_eligibility': 'No',
            'old_active_status': 'Active',
            'new_active_status': 'Inactive',
            'old_title': 'Postdoctoral Scholar',
            'new_title': 'Postdoctoral Scholar',
            'old_primary_affiliation': 'staff',
            'new_primary_affiliation': 'staff',
            'reviewed_by_rdms': False,
            'review_date': None,
            'ncq_expiration_date': None,
            'notes': [],
        },
        {
            'id': 3,
            'date': '2026-09-14',
            'utln': 'kwong03',
            'current_project_owner': True,
            'current_project_approver': True,
            'share_ncq': False,
            'old_ncq_eligibility': 'No',
            'new_ncq_eligibility': 'No',
            'old_active_status': 'Active',
            'new_active_status': 'Inactive',
            'old_title': 'Graduate Student',
            'new_title': 'Alumni',
            'old_primary_affiliation': 'student',
            'new_primary_affiliation': 'alumni',
            'reviewed_by_rdms': False,
            'review_date': None,
            'ncq_expiration_date': None,
            'notes': [
                {'timestamp': '2026-09-14T11:02:00', 'note': 'PI requested extension pending grant renewal.'},
                {'timestamp': '2026-09-15T08:30:00', 'note': 'Grant renewal confirmed by RA office.'},
            ],
        },
    ]

    _records = copy.deepcopy(_SEED_RECORDS)

    @classmethod
    def reset(cls):
        """Restore the in-memory dataset to its original seed state."""
        cls._records = copy.deepcopy(cls._SEED_RECORDS)
        logger.info('Dummy status change dataset reset to seed data.')

    def get_pending_reviews(self):
        return [record for record in self._records if not record['reviewed_by_rdms']]

    def _get_record(self, record_id):
        record = next((r for r in self._records if str(r['id']) == str(record_id)), None)
        if record is None:
            raise StatusChangeAPIError(f"No status change record found with id '{record_id}'.")
        return record

    def acknowledge(self, record_id, reviewer, note=None):
        record = self._get_record(record_id)
        record['reviewed_by_rdms'] = True
        record['review_date'] = datetime.now().isoformat()
        logger.info(f"Status change record {record_id} acknowledged by '{reviewer}'.")
        if note:
            self.add_note(record_id, note)

    def grant_grace_period(self, record_id, reviewer, expiration_date, note=None):
        record = self._get_record(record_id)
        record['reviewed_by_rdms'] = True
        record['review_date'] = datetime.now().isoformat()
        record['ncq_expiration_date'] = expiration_date
        logger.info(
            f"Grace period until {expiration_date} granted for status change record {record_id} by '{reviewer}'."
        )
        if note:
            self.add_note(record_id, note)

    def add_note(self, record_id, note):
        record = self._get_record(record_id)
        record.setdefault('notes', []).append({'timestamp': datetime.now().isoformat(), 'note': note})


def get_status_change_client():
    """
    Returns the client used to interact with the remote storage_user_status_change data source.
    Currently always returns the dummy in-memory client since the remote API is not yet available.
    """
    return DummyStatusChangeAPIClient()
