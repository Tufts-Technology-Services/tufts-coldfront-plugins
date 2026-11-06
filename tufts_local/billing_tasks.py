# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""django-q tasks that depend on coldfront_billing.

Everything in tufts_local that touches coldfront_billing lives here, so the rest of the plugin
(tasks.py included) has no billing imports.
"""

import logging

from coldfront_billing.models import NoCostQuota, NoCostQuotaAllotment
from coldfront_billing.models.no_cost_quota import quota_with_remaining
from coldfront_billing.reports import get_all_storage_allocations, get_cost_per_allocation, missing_billing_code_report
from coldfront_billing.reports.nocostquota import no_cost_quotas_report
from coldfront_billing.reports.quota import auto_assign_quota
from coldfront_utils import mail_json_report
from django.contrib.auth.models import Group, User

from tufts_local.analytics_utils import get_ncq_eligibility
from tufts_local.utils import create_user, setup_custom_logger

logger = logging.getLogger(__name__)


class BillingReportsFailedError(Exception):
    """One or more of send_reports' reports raised. The message carries every report's outcome."""


def send_reports():
    """Send every data engineer billing report, keyed by task name in the result.

    Quotas are assigned first because the allotment-based billing reports depend on it.

    A report that raises doesn't stop the ones after it: its error is recorded in its place.
    If any did, the task raises at the end, so it shows as failed on the task report, with
    the outcome of every report in the error message.
    """
    auto_assign_quota()
    results = {}
    failures = 0
    for send_report in (
        send_allotments_report,
        send_no_cost_quota_report,
        send_storage_allocations_report,
        send_cost_per_allocation_report,
        send_missing_billing_codes_report,
    ):
        try:
            results[send_report.__name__] = send_report()
        except Exception as error:
            logger.exception(f"{send_report.__name__} failed.")
            results[send_report.__name__] = {"error": f"{type(error).__name__}: {error}"}
            failures += 1
    if failures:
        raise BillingReportsFailedError(f"{failures} of {len(results)} billing reports failed: {results}")
    return results


def refresh_ncq_eligibility():
    ncq_logger = setup_custom_logger("ncq", "ncq.log")
    eligibility_data = get_ncq_eligibility()
    tier1_group_name = "faculty_pi_eligible_tier1"
    tier2_group_name = "faculty_pi_eligible_tier2"
    group_tier1 = Group.objects.get(name=tier1_group_name)
    group_tier2 = Group.objects.get(name=tier2_group_name)
    tier1_users = User.objects.filter(groups=group_tier1).values_list("username", flat=True)
    tier2_users = User.objects.filter(groups=group_tier2).values_list("username", flat=True)
    for entry in eligibility_data:
        is_eligible = entry.get("no_cost_quota_eligible", "No")
        username = entry.get("username", "").lower()
        in_tier_1 = username in tier1_users
        in_tier_2 = username in tier2_users
        if is_eligible.lower().strip() == "yes":
            if not in_tier_1:
                ncq_logger.info(f"Adding user {username} to group {tier1_group_name}")
                user = create_user(username)
                user.groups.add(group_tier1)
            if not in_tier_2:
                ncq_logger.info(f"Adding user {username} to group {tier2_group_name}")
                user = create_user(username)
                user.groups.add(group_tier2)
        else:
            if in_tier_1:
                ncq_logger.info(f"Removing user {username} from group {tier1_group_name}")
                user = create_user(username)
                user.groups.remove(group_tier1)
                # delete the allotments associated with this user first so that we can log the details of what is being deleted
                allotments = NoCostQuotaAllotment.objects.filter(no_cost_quota__user=user)
                if allotments.exists():
                    ncq_logger.info(
                        f"User {username} is no longer eligible for NoCostQuota. Deleting {allotments.count()} associated NoCostQuotaAllotment(s)."
                    )
                    for allotment in allotments:
                        ncq_logger.info(
                            f"Deleting NoCostQuotaAllotment of size {allotment.amount_tb} TB associated with allocation {allotment.allocation.id}."
                        )
                        allotment.delete()
                NoCostQuota.objects.filter(user=user).delete()
            if in_tier_2:
                ncq_logger.info(f"Removing user {username} from group {tier2_group_name}")
                user = create_user(username)
                user.groups.remove(group_tier2)
                # delete the allotments associated with this user first so that we can log the details of what is being deleted
                allotments = NoCostQuotaAllotment.objects.filter(no_cost_quota__user=user)
                if allotments.exists():
                    ncq_logger.info(
                        f"User {username} is no longer eligible for NoCostQuota. Deleting {allotments.count()} associated NoCostQuotaAllotment(s)."
                    )
                    for allotment in allotments:
                        ncq_logger.info(
                            f"Deleting NoCostQuotaAllotment of size {allotment.amount_tb} TB associated with allocation {allotment.allocation.id}."
                        )
                        allotment.delete()
                NoCostQuota.objects.filter(user=user).delete()


def send_allotments_report():
    return mail_json_report("Allotments report", "allotments", allotments_report())


def send_no_cost_quota_report():
    return mail_json_report("No cost quotas report", "no_cost_quotas", no_cost_quota_report())


def send_storage_allocations_report():
    return mail_json_report("Storage allocation report", "storage_allocations", allocations_report())


def send_cost_per_allocation_report():
    rows = get_cost_per_allocation()
    for row in rows:
        row["cost"] = str(row["cost"])
    return mail_json_report("Cost per allocation report", "cost_per_allocation", rows)


def send_missing_billing_codes_report():
    missing = missing_billing_code_report()
    rows = [
        {"owner": item["project"].pi.username, "project": item["project"].title, "project_cost": item["project_cost"]}
        for item in missing.get("missing_billing_code", [])
    ]
    return mail_json_report("Missing billing code report", "missing_billing_codes", rows)


def allocations_report():
    return get_all_storage_allocations()


def allotments_report():
    report = no_cost_quotas_report()
    for row in report["allocations"]:
        del row["allocation"]  # remove allocation object from report to avoid serialization issues
    return report["allocations"]


def no_cost_quota_report():
    return [
        {
            "user": quota.user.username,
            "quota_type": quota.quota_type,
            "amount": quota.amount_tb,
            "remaining": round(quota.remaining, 2),
        }
        for quota in quota_with_remaining.all()
    ]
