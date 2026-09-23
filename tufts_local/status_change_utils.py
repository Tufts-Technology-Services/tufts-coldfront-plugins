# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging

import requests

from coldfront.config.settings import ENV

logger = logging.getLogger(__name__)

# the service rejects a rows above 500 (see ROWS_PARAM in its secure router)
MAX_ROWS = 500
REQUEST_TIMEOUT = 30

# the values active_status_old/new can take. The remote column is a bare CHAR(1) with no
# constraint and the service documents no enum, so this list is the only record of it.
ACTIVE_STATUS_VALUES = ('A', 'P', 'F')


class StatusChangeAPIError(Exception):
    pass


class StatusChangeAPIClient:
    """
    Client for the remote storage_user_status_change data source (rt-analytics-api).

    Records are keyed by (user, record_date): the remote table has a composite primary key
    and no surrogate id, so every method that touches a single record takes both. Record
    fields are passed through from the service unchanged, so what the templates read is what
    the API documents -- note that its boolean-ish columns hold the strings 'Yes' and 'No',
    not JSON booleans.
    """

    def __init__(self, base_url=None, token=None):
        self.base_url = (base_url or '').rstrip('/')
        self.token = token

    def _path(self, *parts):
        return '/'.join(['storage-owner-status-change', *(str(part) for part in parts)])

    def _request(self, method, path, **kwargs):
        """
        Issue one request, turning transport and HTTP errors into StatusChangeAPIError.

        Returns None on 404: the service answers that way both for a record that doesn't
        exist and for a query that matched nothing, so the caller decides which it meant.
        """
        if not self.base_url:
            raise StatusChangeAPIError('RT_ANALYTICS_BASE_URL is not set; cannot reach the status-change service.')
        try:
            response = requests.request(
                method,
                f'{self.base_url}/{path}',
                headers={'X-API-Key': self.token or ''},
                timeout=REQUEST_TIMEOUT,
                **kwargs,
            )
        except requests.RequestException as e:
            raise StatusChangeAPIError(f'Could not reach the status-change service: {e}') from e
        if response.status_code == 404:
            return None
        if not response.ok:
            raise StatusChangeAPIError(f'Status-change service returned {response.status_code}: {response.text[:200]}')
        try:
            return response.json()
        except ValueError as e:
            raise StatusChangeAPIError(f'Status-change service returned a non-JSON response: {e}') from e

    def get_pending_reviews(self, include_acknowledged=False, start=0, rows=50):
        """
        One page of status changes awaiting RDMS review, newest first.

        Each record carries that user's notes under 'notes', newest first -- the service
        attaches them, so there is no extra call per record. fetch_all_pending_reviews()
        walks every page.
        """
        params = {'start': start, 'rows': min(rows, MAX_ROWS)}
        if not include_acknowledged:
            params['reviewed_by_rdms'] = 'No'
        page = self._request('GET', self._path(), params=params)
        # the service 404s instead of returning an empty page, both when nothing at all is
        # pending and when start has run off the end of the results
        if page is None:
            return []
        return page.get('results', [])

    def _update(self, user, record_date, reviewer, **fields):
        """
        PATCH one record. Fields left as None are omitted rather than sent as null.

        The service rejects a body holding nothing but author_utln, so at least one of
        reviewed_by_rdms, ncq_expiration_date or note has to survive that filtering.
        """
        payload = {'author_utln': reviewer}
        payload.update({key: value for key, value in fields.items() if value is not None})
        result = self._request('PATCH', self._path(user, record_date), json=payload)
        if result is None:
            raise StatusChangeAPIError(f"No status change record found for '{user}' on {record_date}.")
        return result

    def acknowledge(self, user, record_date, reviewer, note=None):
        """Mark a change reviewed. The service stamps review_date and adds a 'Reviewed' note."""
        self._update(user, record_date, reviewer, reviewed_by_rdms='Yes', note=note)
        logger.info(f"Status change for '{user}' on {record_date} acknowledged by '{reviewer}'.")

    def grant_grace_period(self, user, record_date, reviewer, expiration_date, note=None):
        """
        Extend NCQ eligibility to expiration_date (ISO 8601).

        Sending ncq_expiration_date also marks the record reviewed and adds a
        'Grace period granted until <date>' note, both service-side.
        """
        if not expiration_date:
            raise StatusChangeAPIError('An expiration date is required to grant a grace period.')
        self._update(user, record_date, reviewer, ncq_expiration_date=expiration_date, note=note)
        logger.info(f"Grace period until {expiration_date} granted for '{user}' by '{reviewer}'.")

    def add_note(self, user, record_date, note, reviewer):
        """
        Attach a note to a user, leaving the review status alone.

        Notes hang off the username rather than off one change, but a PATCH on a change is
        the only way to write one, so the date is still needed to address the request.
        """
        if not note:
            raise StatusChangeAPIError('Note text is required.')
        self._update(user, record_date, reviewer, note=note)


def fetch_all_pending_reviews(client, include_acknowledged=False):
    """
    Every pending review, walking the client's pagination.

    The queue is normally short, but it is a queue: showing only its first page would hide
    work rather than defer it. Pass include_acknowledged to get reviewed records too.
    """
    records = []
    while True:
        page = client.get_pending_reviews(include_acknowledged=include_acknowledged, start=len(records), rows=MAX_ROWS)
        if not page:
            return records
        records.extend(page)


def get_status_change_client():
    """
    Returns the client used to interact with the remote storage_user_status_change data source.

    There is deliberately no in-process alternative: the in-memory stand-in used by the tests
    lives in tufts_local.tests.fakes, where application code can't reach for it.
    """
    return StatusChangeAPIClient(
        base_url=ENV.str('RT_ANALYTICS_BASE_URL', default=''),
        token=ENV.str('RT_ANALYTICS_API_KEY', default=''),
    )
