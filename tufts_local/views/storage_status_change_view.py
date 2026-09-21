import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.views.decorators.http import require_http_methods

from tufts_local.status_change_utils import StatusChangeAPIError, get_status_change_client

logger = logging.getLogger(__name__)


@login_required
@user_passes_test(lambda u: u.is_superuser)
@require_http_methods(['GET', 'POST'])
def storage_status_change_review(request):
    client = get_status_change_client()

    if request.method == 'POST':
        record_id = request.POST.get('record_id')
        action = request.POST.get('action')
        note = request.POST.get('notes', '').strip() or None
        try:
            if action == 'acknowledge':
                client.acknowledge(record_id, reviewer=request.user.username, note=note)
                messages.success(request, f'Acknowledged status change for {record_id}.')
            elif action == 'grace-period':
                expiration_date = request.POST.get('ncq_expiration_date')
                client.grant_grace_period(
                    record_id,
                    reviewer=request.user.username,
                    expiration_date=expiration_date,
                    note=note,
                )
                messages.success(request, f'Grace period granted for {record_id}.')
            elif action == 'note':
                if note:
                    client.add_note(record_id, note, user=request.user.username)
                    messages.success(request, f'Note saved for {record_id}.')
                else:
                    messages.error(request, 'Note text is required.')
        except StatusChangeAPIError as e:
            logger.error(f'Error updating status change record {record_id}: {e}')
            messages.error(request, f'Could not update record: {e}')
        return redirect('storage-status-change-review')

    try:
        records = client.get_pending_reviews()
    except StatusChangeAPIError as e:
        logger.error(f'Error fetching pending status change reviews: {e}')
        records = []
        messages.error(request, f'Could not reach status-change service: {e}')

    return TemplateResponse(request, 'tufts_local/storage_status_change_review.html', {'records': records})


@login_required
@user_passes_test(lambda u: u.is_superuser)
@require_http_methods(['POST'])
def storage_status_change_reset_demo_data(request):
    client = get_status_change_client()
    reset = getattr(client, 'reset', None)
    if callable(reset):
        reset()
        messages.success(request, 'Demo data has been reset.')
    else:
        messages.error(request, 'Reset is not supported for the current data source.')
    return redirect('storage-status-change-review')
