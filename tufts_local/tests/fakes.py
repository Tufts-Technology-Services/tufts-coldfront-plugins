# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Test doubles for the remote services tufts_local talks to.

DummyStatusChangeAPIClient lives here rather than beside the real client so it can't be
reached from application code: a stand-in that ships in the app is one env var away from
serving invented records to someone who believes they are looking at live data.
"""

import copy
import datetime
import uuid

import redis
from django.utils import timezone
from django_q import core_signing
from django_q.conf import Conf
from django_q.signing import PickleSerializer
from django_q.status import Status

from tufts_local.status_change_utils import MAX_ROWS, StatusChangeAPIClient, StatusChangeAPIError


class DummyStatusChangeAPIClient(StatusChangeAPIClient):
    """
    In-memory stand-in for the remote service, for testing the review page without HTTP.

    Mirrors the real client's interface, record shape and write-side behaviour, including
    the notes the service writes on its own. The seed records are shaped exactly like the
    service's, so a test passing here means the page handles what the API really returns.
    """

    _SEED_RECORDS = [
        {
            'date': '2026-09-10',
            'username': 'jdoe01',
            'full_name': 'Jane Q Doe',
            'email': 'jane.doe@tufts.edu',
            'current_project_owner': 'Yes',
            'current_project_approver': 'No',
            'current_ncq_sharer': 'Yes',
            'ncq_eligible_old': 'Yes',
            'ncq_eligible_new': 'No',
            'active_status_old': 'A',
            'active_status_new': 'A',
            'title_old': 'Research Assistant Professor',
            'title_new': 'Emeritus',
            'primary_affiliation_old': 'faculty',
            'primary_affiliation_new': 'affiliate',
            'reviewed_by_rdms': 'No',
            'review_date': None,
            'ncq_expiration_date': None,
            'notes': [
                {
                    'id': 1,
                    'note_timestamp': '2026-09-10T09:15:00',
                    'username': 'jdoe01',
                    'author_utln': 'system',
                    'note': 'Flagged by nightly AD sync job.',
                },
            ],
        },
        {
            'date': '2026-09-12',
            'username': 'asmith02',
            'full_name': 'Alex Smith',
            'email': 'alex.smith@tufts.edu',
            'current_project_owner': 'No',
            'current_project_approver': 'Yes',
            'current_ncq_sharer': 'Yes',
            'ncq_eligible_old': 'Yes',
            'ncq_eligible_new': 'No',
            'active_status_old': 'A',
            'active_status_new': 'F',
            'title_old': 'Postdoctoral Scholar',
            'title_new': 'Postdoctoral Scholar',
            'primary_affiliation_old': 'staff',
            'primary_affiliation_new': 'staff',
            'reviewed_by_rdms': 'No',
            'review_date': None,
            'ncq_expiration_date': None,
            'notes': [],
        },
        {
            'date': '2026-09-14',
            'username': 'kwong03',
            'full_name': 'Kelly Wong',
            'email': 'kelly.wong@tufts.edu',
            'current_project_owner': 'Yes',
            'current_project_approver': 'Yes',
            'current_ncq_sharer': 'No',
            'ncq_eligible_old': 'No',
            'ncq_eligible_new': 'No',
            'active_status_old': 'A',
            'active_status_new': 'F',
            'title_old': 'Graduate Student',
            'title_new': 'Alumni',
            'primary_affiliation_old': 'student',
            'primary_affiliation_new': 'alumni',
            'reviewed_by_rdms': 'No',
            'review_date': None,
            'ncq_expiration_date': None,
            'notes': [
                {
                    'id': 3,
                    'note_timestamp': '2026-09-15T08:30:00',
                    'username': 'kwong03',
                    'author_utln': 'jsmith',
                    'note': 'Grant renewal confirmed by RA office.',
                },
                {
                    'id': 2,
                    'note_timestamp': '2026-09-14T11:02:00',
                    'username': 'kwong03',
                    'author_utln': 'jsmith',
                    'note': 'PI requested extension pending grant renewal.',
                },
            ],
        },
    ]

    _records = copy.deepcopy(_SEED_RECORDS)

    @classmethod
    def reset(cls):
        """Restore the in-memory dataset to its original seed state."""
        cls._records = copy.deepcopy(cls._SEED_RECORDS)

    def get_pending_reviews(self, include_acknowledged=False, start=0, rows=50):
        records = [r for r in self._records if include_acknowledged or r['reviewed_by_rdms'] != 'Yes']
        return records[start : start + min(rows, MAX_ROWS)]

    def _get_record(self, user, record_date):
        record = next(
            (r for r in self._records if r['username'] == user and str(r['date']) == str(record_date)),
            None,
        )
        if record is None:
            raise StatusChangeAPIError(f"No status change record found for '{user}' on {record_date}.")
        return record

    def _update(self, user, record_date, reviewer, **fields):
        """Apply, against the local dict, the same field and note rules the service applies."""
        if all(fields.get(key) is None for key in ('reviewed_by_rdms', 'ncq_expiration_date', 'note')):
            raise StatusChangeAPIError('No fields to update.')
        record = self._get_record(user, record_date)

        expiration_date = fields.get('ncq_expiration_date')
        reviewed = 'Yes' if expiration_date is not None else fields.get('reviewed_by_rdms')
        newly_reviewed = reviewed == 'Yes' and record['reviewed_by_rdms'] == 'No'
        if reviewed is not None:
            if newly_reviewed:
                record['review_date'] = datetime.date.today().isoformat()
            record['reviewed_by_rdms'] = reviewed
        if expiration_date is not None:
            record['ncq_expiration_date'] = expiration_date

        if expiration_date is not None:
            self._add_note_to(record, f'Grace period granted until {expiration_date}', reviewer)
        elif newly_reviewed:
            self._add_note_to(record, 'Reviewed', reviewer)
        if fields.get('note') is not None:
            self._add_note_to(record, fields['note'], reviewer)
        return record

    @staticmethod
    def _add_note_to(record, note, reviewer):
        # newest first, matching the order the service returns notes in
        record.setdefault('notes', []).insert(
            0,
            {
                'id': None,
                'note_timestamp': datetime.datetime.now().isoformat(),
                'username': record['username'],
                'author_utln': reviewer,
                'note': note,
            },
        )


class FakeBroker:
    """
    Stand-in for django-q's broker, for the queue health check, holding real signed heartbeats.

    add_cluster() signs a heartbeat the way a running cluster's Stat.save() does, so the
    check under test decodes it with django-q's own Stat.get_all(), signature check included.
    """

    def __init__(self):
        self.reachable = True
        self.queued = 0
        self.heartbeats = []

    def queue_size(self):
        # the health check's first command, so the one that finds an unreachable broker
        if not self.reachable:
            raise redis.ConnectionError('Error 111 connecting to redis-host:6379. Connection refused.')
        return self.queued

    def info(self):
        return 'Redis 7.2.4'

    def get_stats(self, pattern):
        return list(self.heartbeats)

    def add_cluster(self, host='worker-host-a', workers=4, status=None, started=None, secret_key=None):
        heartbeat = Status(pid=1000 + len(self.heartbeats), cluster_id=uuid.uuid4())
        heartbeat.host = host
        heartbeat.status = Conf.IDLE if status is None else status
        heartbeat.workers = list(range(2000, 2000 + workers))
        heartbeat.tob = started or timezone.now() - datetime.timedelta(days=3)
        # SignedPackage.dumps, but with the key a cluster configured differently would sign with
        pack = core_signing.dumps(
            heartbeat,
            key=secret_key or Conf.SECRET_KEY,
            salt=Conf.PREFIX,
            compress=True,
            serializer=PickleSerializer,
        )
        self.heartbeats.append(pack)
        return heartbeat
