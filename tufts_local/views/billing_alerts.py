# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Acknowledging a billing alert. Imports coldfront_billing, so it's kept out of views/__init__ and
tufts_local.urls routes to it only when billing is installed.
"""

import logging

from coldfront_billing.models import NoCostQuotaAllotmentFailure
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from tufts_local.alerts import forget_current_alerts, superusers

logger = logging.getLogger(__name__)


@login_required
@require_POST
@user_passes_test(superusers)
def acknowledge_allotment_failure(request, pk):
    """
    Mark a failed no-cost quota allotment dealt with, so it stops raising an alert.

    For when someone has applied it by hand some other way, or decided it shouldn't be. Saving an
    allotment from the same quota resolves it without this. A second click is a no-op that keeps
    the first person's acknowledgement.
    """
    failure = get_object_or_404(NoCostQuotaAllotmentFailure, pk=pk)
    reason = request.POST.get("reason", "").strip()
    if failure.resolve(request.user, reason):
        logger.info(
            f"No-cost quota allotment failure {pk} on allocation {failure.allocation_id} acknowledged by '{request.user.username}'."
        )
        forget_current_alerts()
        messages.success(
            request, f"Acknowledged the failed no-cost quota allotment on allocation {failure.allocation_id}."
        )
    else:
        messages.info(request, "That failure had already been dealt with.")

    next_path = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(
        next_path, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_path = reverse("alert-list")
    return redirect(next_path)
