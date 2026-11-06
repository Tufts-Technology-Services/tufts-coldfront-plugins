# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import datetime
import json
import logging
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from coldfront.core.allocation.models import Allocation, AllocationStatusChoice

# coldfront_billing is private, and tests/settings.py installs it only where it's available
billing_tasks = pytest.importorskip("tufts_local.billing_tasks")

from coldfront_billing.models import NoCostQuota, NoCostQuotaAllotment  # noqa: E402

DATA_ENGINEER = "data.engineer@tufts.edu"

REPORT_TASKS = (
    "send_allotments_report",
    "send_no_cost_quota_report",
    "send_storage_allocations_report",
    "send_cost_per_allocation_report",
    "send_missing_billing_codes_report",
)


@pytest.fixture
def mail(settings, mailoutbox):
    """A deployment configured to send reports, and the outbox to read them out of."""
    settings.REPORTING_EMAIL_ENABLED = True
    settings.EMAIL_SENDER = "coldfront@tufts.edu"
    settings.EMAIL_SUBJECT_PREFIX = "[ColdFront]"
    settings.REPORTING_EMAIL = DATA_ENGINEER
    return mailoutbox


def standing_in_for(task_name, fake):
    """`fake`, named as the task it replaces: send_reports labels each outcome by task name."""
    fake.__name__ = task_name
    return fake


def quotas_in_queryset(*quotas):
    """A stand-in for quota_with_remaining: the report reads it through .all(), as a queryset."""
    return SimpleNamespace(all=lambda: list(quotas))


def attached_rows(mailoutbox):
    """The rows the last message carried, read back out of its JSON attachment."""
    _filename, content, _mimetype = mailoutbox[-1].attachments[0]
    return json.loads(content)


class TestSendReports:
    @pytest.fixture
    def calls(self, monkeypatch):
        """Swap quota assignment and every report for fakes that record the order they ran in."""
        calls = []
        monkeypatch.setattr(billing_tasks, "auto_assign_quota", lambda: calls.append("auto_assign_quota"))
        for name in REPORT_TASKS:

            def fake_report(name=name):
                calls.append(name)
                return {"message": f"{name} sent"}

            monkeypatch.setattr(billing_tasks, name, standing_in_for(name, fake_report))
        return calls

    def test_assigns_quotas_before_any_report(self, calls):
        """The allotments the reports describe are the ones auto_assign_quota just made."""
        billing_tasks.send_reports()

        assert calls == ["auto_assign_quota", *REPORT_TASKS]

    def test_returns_each_reports_result_under_its_task_name(self, calls):
        result = billing_tasks.send_reports()

        assert result == {name: {"message": f"{name} sent"} for name in REPORT_TASKS}

    def test_a_failing_report_does_not_stop_the_rest(self, calls, monkeypatch):
        def broken_report():
            calls.append("send_no_cost_quota_report")
            raise RuntimeError("SMTP is down")

        monkeypatch.setattr(
            billing_tasks, "send_no_cost_quota_report", standing_in_for("send_no_cost_quota_report", broken_report)
        )

        with pytest.raises(billing_tasks.BillingReportsFailedError):
            billing_tasks.send_reports()

        assert calls == ["auto_assign_quota", *REPORT_TASKS]

    def test_a_failure_fails_the_task_with_every_reports_outcome(self, calls, monkeypatch):
        """django-q marks a task failed only if it raises, and stores the message as the
        result, so the task report shows what failed and what still went out."""

        def broken_report():
            raise RuntimeError("SMTP is down")

        monkeypatch.setattr(
            billing_tasks, "send_no_cost_quota_report", standing_in_for("send_no_cost_quota_report", broken_report)
        )

        with pytest.raises(billing_tasks.BillingReportsFailedError) as failure:
            billing_tasks.send_reports()

        message = str(failure.value)
        assert message.startswith("1 of 5 billing reports failed")
        assert "'send_no_cost_quota_report': {'error': 'RuntimeError: SMTP is down'}" in message
        assert "'send_allotments_report': {'message': 'send_allotments_report sent'}" in message

    def test_a_failing_report_is_logged_with_its_traceback(self, calls, monkeypatch, caplog):
        def broken_report():
            raise RuntimeError("SMTP is down")

        monkeypatch.setattr(
            billing_tasks,
            "send_cost_per_allocation_report",
            standing_in_for("send_cost_per_allocation_report", broken_report),
        )

        with caplog.at_level(logging.ERROR, logger="tufts_local.billing_tasks"):
            with pytest.raises(billing_tasks.BillingReportsFailedError):
                billing_tasks.send_reports()

        (record,) = caplog.records
        assert record.getMessage() == "send_cost_per_allocation_report failed."
        assert record.exc_info is not None

    def test_a_failure_to_assign_quotas_sends_nothing(self, calls, monkeypatch):
        """Reports built on allotments that weren't assigned would be wrong, not just late."""

        def broken_assignment():
            raise RuntimeError("database is locked")

        monkeypatch.setattr(billing_tasks, "auto_assign_quota", broken_assignment)

        with pytest.raises(RuntimeError, match="database is locked"):
            billing_tasks.send_reports()

        assert calls == []


class TestReportAttachments:
    @pytest.mark.parametrize(
        ("task_name", "title", "filename_stem"),
        [
            ("send_allotments_report", "Allotments report", "allotments"),
            ("send_no_cost_quota_report", "No cost quotas report", "no_cost_quotas"),
            ("send_storage_allocations_report", "Storage allocation report", "storage_allocations"),
            ("send_cost_per_allocation_report", "Cost per allocation report", "cost_per_allocation"),
            ("send_missing_billing_codes_report", "Missing billing code report", "missing_billing_codes"),
        ],
    )
    def test_each_report_goes_out_as_dated_json(self, task_name, title, filename_stem, mail, monkeypatch):
        monkeypatch.setattr(billing_tasks, "no_cost_quotas_report", lambda: {"allocations": []})
        monkeypatch.setattr(billing_tasks, "quota_with_remaining", quotas_in_queryset())
        monkeypatch.setattr(billing_tasks, "get_all_storage_allocations", list)
        monkeypatch.setattr(billing_tasks, "get_cost_per_allocation", list)
        monkeypatch.setattr(billing_tasks, "missing_billing_code_report", dict)
        today = datetime.date.today().isoformat()

        result = getattr(billing_tasks, task_name)()

        assert mail[0].to == [DATA_ENGINEER]
        assert mail[0].subject == f"[ColdFront] {title} {today}"
        filename, content, mimetype = mail[0].attachments[0]
        assert (filename, mimetype) == (f"{filename_stem}_{today}.json", "application/json")
        assert json.loads(content) == []
        assert result == {"message": f"0 rows sent to {DATA_ENGINEER}"}

    def test_allotments_leave_out_the_allocation_object(self, mail, monkeypatch):
        """The Allocation instance can't be written as JSON, and the rows already name it."""
        report = {"allocations": [{"allocation": object(), "allocation_id": 7, "amount_tb": Decimal("2.5")}]}
        monkeypatch.setattr(billing_tasks, "no_cost_quotas_report", lambda: report)

        billing_tasks.send_allotments_report()

        assert attached_rows(mail) == [{"allocation_id": 7, "amount_tb": 2.5}]

    def test_no_cost_quotas_report_the_remaining_amount_to_two_places(self, mail, monkeypatch):
        quota = SimpleNamespace(
            user=SimpleNamespace(username="smith01"),
            quota_type="tier2",
            amount_tb=Decimal("5.000"),
            remaining=Decimal("1.23456"),
        )
        monkeypatch.setattr(billing_tasks, "quota_with_remaining", quotas_in_queryset(quota))

        billing_tasks.send_no_cost_quota_report()

        assert attached_rows(mail) == [{"user": "smith01", "quota_type": "tier2", "amount": 5.0, "remaining": 1.23}]

    def test_storage_allocations_are_sent_as_billing_reports_them(self, mail, monkeypatch):
        rows = [{"allocation_id": 7, "quota_tb": 10}]
        monkeypatch.setattr(billing_tasks, "get_all_storage_allocations", lambda: rows)

        billing_tasks.send_storage_allocations_report()

        assert attached_rows(mail) == rows

    def test_costs_are_written_as_exact_strings(self, mail, monkeypatch):
        """A cost is money: as a string it keeps the cents a float could round away."""
        monkeypatch.setattr(
            billing_tasks, "get_cost_per_allocation", lambda: [{"allocation_id": 7, "cost": Decimal("12.50")}]
        )

        billing_tasks.send_cost_per_allocation_report()

        assert attached_rows(mail) == [{"allocation_id": 7, "cost": "12.50"}]

    def test_missing_billing_codes_name_the_owner_and_project(self, mail, monkeypatch):
        project = SimpleNamespace(title="Ocean Modelling", pi=SimpleNamespace(username="pi01"))
        monkeypatch.setattr(
            billing_tasks,
            "missing_billing_code_report",
            lambda: {"missing_billing_code": [{"project": project, "project_cost": Decimal("40.00")}]},
        )

        billing_tasks.send_missing_billing_codes_report()

        assert attached_rows(mail) == [{"owner": "pi01", "project": "Ocean Modelling", "project_cost": 40.0}]

    def test_no_missing_billing_codes_sends_an_empty_list(self, mail, monkeypatch):
        """billing leaves the key out when nothing is missing; the report still goes out."""
        monkeypatch.setattr(billing_tasks, "missing_billing_code_report", dict)

        result = billing_tasks.send_missing_billing_codes_report()

        assert attached_rows(mail) == []
        assert result == {"message": f"0 rows sent to {DATA_ENGINEER}"}


@pytest.mark.django_db
class TestNoCostQuotaReportFreshness:
    def test_each_run_reads_the_quotas_as_they_are_now(self, mail):
        """quota_with_remaining is one queryset per process, and a queryset caches what it
        read the first time it's iterated. A qcluster worker lives across many runs, so a
        report that iterated it directly would send the first run's quotas forever."""
        smith = get_user_model().objects.create(username="smith01")
        NoCostQuota.objects.create(user=smith, quota_type="tier2", amount_tb=Decimal("5"))
        billing_tasks.send_no_cost_quota_report()

        jones = get_user_model().objects.create(username="jones02")
        NoCostQuota.objects.create(user=jones, quota_type="tier2", amount_tb=Decimal("3"))
        NoCostQuota.objects.filter(user=smith).update(amount_tb=Decimal("8"))
        billing_tasks.send_no_cost_quota_report()

        rows = sorted(attached_rows(mail), key=lambda row: row["user"])
        assert [(row["user"], row["amount"], row["remaining"]) for row in rows] == [
            ("jones02", 3.0, 3.0),
            ("smith01", 8.0, 8.0),
        ]


@pytest.mark.django_db
class TestRefreshNcqEligibility:
    @pytest.fixture
    def tiers(self):
        return (
            Group.objects.create(name="faculty_pi_eligible_tier1"),
            Group.objects.create(name="faculty_pi_eligible_tier2"),
        )

    @pytest.fixture
    def eligibility(self, monkeypatch):
        """Feed the task eligibility rows, and keep it off AD and out of a real ncq.log."""
        rows = []
        monkeypatch.setattr(billing_tasks, "get_ncq_eligibility", lambda: rows)
        monkeypatch.setattr(
            billing_tasks,
            "create_user",
            lambda username: get_user_model().objects.get_or_create(username=username.lower().strip())[0],
        )
        monkeypatch.setattr(
            billing_tasks, "setup_custom_logger", lambda name, log_file: logging.getLogger(f"tufts_local.tests.{name}")
        )
        return rows

    def group_names(self, username):
        return set(get_user_model().objects.get(username=username).groups.values_list("name", flat=True))

    def test_an_eligible_user_joins_both_tiers(self, tiers, eligibility):
        eligibility.append({"username": "smith01", "no_cost_quota_eligible": "Yes"})

        billing_tasks.refresh_ncq_eligibility()

        assert self.group_names("smith01") == {"faculty_pi_eligible_tier1", "faculty_pi_eligible_tier2"}

    def test_eligibility_ignores_case_and_surrounding_space(self, tiers, eligibility):
        eligibility.append({"username": "Smith01", "no_cost_quota_eligible": " YES "})

        billing_tasks.refresh_ncq_eligibility()

        assert self.group_names("smith01") == {"faculty_pi_eligible_tier1", "faculty_pi_eligible_tier2"}

    def test_a_row_without_an_answer_counts_as_ineligible(self, tiers, eligibility):
        user = get_user_model().objects.create(username="smith01")
        user.groups.add(*tiers)
        eligibility.append({"username": "smith01"})

        billing_tasks.refresh_ncq_eligibility()

        assert self.group_names("smith01") == set()

    def test_an_ineligible_user_loses_both_tiers_and_their_quotas(self, tiers, eligibility, project):
        user = get_user_model().objects.create(username="smith01")
        user.groups.add(*tiers)
        quota = NoCostQuota.objects.create(user=user, quota_type="tier2", amount_tb=Decimal("5"))
        allocation = Allocation.objects.create(
            project=project, status=AllocationStatusChoice.objects.get_or_create(name="Active")[0]
        )
        NoCostQuotaAllotment.objects.create(no_cost_quota=quota, allocation=allocation, amount_tb=Decimal("1"))
        eligibility.append({"username": "smith01", "no_cost_quota_eligible": "No"})

        billing_tasks.refresh_ncq_eligibility()

        assert self.group_names("smith01") == set()
        assert not NoCostQuota.objects.filter(user=user).exists()
        assert not NoCostQuotaAllotment.objects.exists()
        assert Allocation.objects.filter(pk=allocation.pk).exists()

    def test_an_ineligible_user_outside_both_tiers_is_left_alone(self, tiers, eligibility):
        """No AD lookup and no user created for someone who was never in a tier."""
        eligibility.append({"username": "jones02", "no_cost_quota_eligible": "No"})

        billing_tasks.refresh_ncq_eligibility()

        assert not get_user_model().objects.filter(username="jones02").exists()
