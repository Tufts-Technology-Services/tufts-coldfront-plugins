# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from tufts_local.status_change_utils import (
    StatusChangeAPIError,
    fetch_all_pending_reviews,
    get_status_change_client,
)

logger = logging.getLogger(__name__)

# query string that turns already-reviewed records back on; off unless asked for, so the
# page opens on the work still outstanding
SHOW_ACKNOWLEDGED_PARAM = 'show_acknowledged'
TRUTHY = {'1', 'true', 'on', 'yes'}


def _show_acknowledged(request):
    return request.GET.get(SHOW_ACKNOWLEDGED_PARAM, '').lower() in TRUTHY


@login_required
@user_passes_test(lambda u: u.is_superuser)
@require_http_methods(['GET', 'POST'])
def storage_status_change_review(request):
    client = get_status_change_client()

    if request.method == 'POST':
        # a record is addressed by (username, date): the remote table has no surrogate id
        user = request.POST.get('username')
        record_date = request.POST.get('record_date')
        action = request.POST.get('action')
        note = request.POST.get('notes', '').strip() or None
        try:
            if action == 'acknowledge':
                client.acknowledge(user, record_date, reviewer=request.user.username, note=note)
                messages.success(request, f'Acknowledged status change for {user}.')
            elif action == 'grace-period':
                expiration_date = request.POST.get('ncq_expiration_date')
                client.grant_grace_period(
                    user,
                    record_date,
                    reviewer=request.user.username,
                    expiration_date=expiration_date,
                    note=note,
                )
                messages.success(request, f'Grace period granted for {user}.')
            elif action == 'note':
                if note:
                    client.add_note(user, record_date, note, reviewer=request.user.username)
                    messages.success(request, f'Note saved for {user}.')
                else:
                    messages.error(request, 'Note text is required.')
        except StatusChangeAPIError as e:
            logger.error(f'Error updating status change record for {user} on {record_date}: {e}')
            messages.error(request, f'Could not update record: {e}')
        # the toggle rides on the POST target so acting on a row doesn't reset the view --
        # and acknowledging while showing everything would otherwise hide the row just acted on
        redirect_to = reverse('storage-status-change-review')
        if request.POST.get(SHOW_ACKNOWLEDGED_PARAM, '').lower() in TRUTHY:
            redirect_to = f'{redirect_to}?{SHOW_ACKNOWLEDGED_PARAM}=1'
        return redirect(redirect_to)

    show_acknowledged = _show_acknowledged(request)
    try:
        records = fetch_all_pending_reviews(client, include_acknowledged=show_acknowledged)
    except StatusChangeAPIError as e:
        logger.error(f'Error fetching pending status change reviews: {e}')
        records = []
        messages.error(request, f'Could not reach status-change service: {e}')

    return TemplateResponse(
        request,
        'tufts_local/storage_status_change_review.html',
        {'records': records, 'show_acknowledged': show_acknowledged},
    )
