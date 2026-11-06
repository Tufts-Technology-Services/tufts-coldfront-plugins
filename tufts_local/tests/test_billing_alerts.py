# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from django.urls import reverse

from coldfront.core.allocation.models import (
    Allocation,
    AllocationAttribute,
    AllocationAttributeType,
    AllocationStatusChoice,
    AttributeType,
)

from tufts_local import alerts

# coldfront_billing is private, and tests/settings.py installs it only where it's available
billing_alerts = pytest.importorskip("tufts_local.billing_alerts")

from coldfront_billing.constants import ALLOCATION_QUOTA_ATTRIBUTE_NAME  # noqa: E402
from coldfront_billing.models import NoCostQuota, NoCostQuotaAllotmentFailure  # noqa: E402

from tufts_local.views.billing_alerts import acknowledge_allotment_failure  # noqa: E402


def make_allocation(project, status="Active"):
    return Allocation.objects.create(
        project=project, status=AllocationStatusChoice.objects.get_or_create(name=status)[0], justification="research"
    )


def make_quota(project):
    return NoCostQuota.objects.create(
        user=project.pi, quota_type="tier_1", description="Tier 1", amount_tb=Decimal("10"), units="TB", increment_gb=10
    )


def make_failure(allocation, quota=None, message="Could not save allotment: amount: too small"):
    return NoCostQuotaAllotmentFailure.objects.create(allocation=allocation, no_cost_quota=quota, message=message)


def make_account(username, **flags):
    return get_user_model().objects.create(username=username, **flags)


@pytest.mark.django_db
@pytest.mark.urls("tufts_local.tests.urls")
class TestAllotmentFailureAlerts:
    def test_nothing_failed_means_no_alert(self, project):
        assert billing_alerts.allotment_failure_alerts() == []

    def test_an_unresolved_failure_is_a_warning(self, project):
        allocation = make_allocation(project)
        quota = make_quota(project)
        failure = make_failure(allocation, quota)

        [alert] = billing_alerts.allotment_failure_alerts()

        assert alert.severity == "warning"
        assert alert.title == f"No-cost quota not applied: Ocean Modelling, allocation {allocation.pk}"
        assert "pi01's tier_1 quota" in alert.detail
        assert failure.message in alert.detail
        # where to apply it by hand
        assert alert.link == f"{reverse('assign_no_cost_quota')}?selected_quota={quota.pk}"
        assert alert.ack_url == reverse("allotment-failure-acknowledge", kwargs={"pk": failure.pk})

    def test_without_a_quota_it_links_to_the_allocation(self, project):
        allocation = make_allocation(project)
        make_failure(allocation, message="The requested no-cost quota 'x' is not a quota id.")

        [alert] = billing_alerts.allotment_failure_alerts()

        assert alert.link == reverse("allocation-detail", kwargs={"pk": allocation.pk})
        assert "a no-cost quota" in alert.detail

    def test_resolved_failures_raise_no_alert(self, project):
        make_failure(make_allocation(project)).resolve()

        assert billing_alerts.allotment_failure_alerts() == []

    def test_one_alert_per_failure_oldest_first(self, project):
        first = make_allocation(project)
        second = make_allocation(project)
        make_failure(first)
        make_failure(second)

        titles = [alert.title for alert in billing_alerts.allotment_failure_alerts()]

        assert titles == [
            f"No-cost quota not applied: Ocean Modelling, allocation {first.pk}",
            f"No-cost quota not applied: Ocean Modelling, allocation {second.pk}",
        ]

    def test_only_superusers_see_them(self, project):
        make_failure(make_allocation(project))
        superuser = make_account("root", is_superuser=True)
        staff = make_account("helpdesk", is_staff=True)

        def sources_seen_by(user):
            return {alert.source for alert in alerts.get_current_alerts(user)[0]}

        assert "no-cost quota allotments" in sources_seen_by(superuser)
        assert "no-cost quota allotments" not in sources_seen_by(staff)

    def test_a_failed_activation_raises_an_alert(self, project):
        """End to end: billing records the failure as the allocation activates, and it shows here."""
        text, _ = AttributeType.objects.get_or_create(name="Text")
        request_type, _ = AllocationAttributeType.objects.get_or_create(
            name=ALLOCATION_QUOTA_ATTRIBUTE_NAME, defaults={"attribute_type": text}
        )
        allocation = make_allocation(project, status="New")
        AllocationAttribute.objects.create(
            allocation=allocation, allocation_attribute_type=request_type, value="not-an-int"
        )

        allocation.status = AllocationStatusChoice.objects.get_or_create(name="Active")[0]
        allocation.save()

        [alert] = billing_alerts.allotment_failure_alerts()
        assert "'not-an-int' is not a quota id" in alert.detail


def post(view, pk, user, **data):
    request = RequestFactory().post(f"/allotment-failure-acknowledge/{pk}/", data)
    request.user = user
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    MessageMiddleware(lambda r: None).process_request(request)
    return view(request, pk)


class TestAcknowledgeAccess:
    def test_anonymous_user_redirects_to_login(self):
        request = RequestFactory().post("/allotment-failure-acknowledge/1/")
        request.user = AnonymousUser()

        response = acknowledge_allotment_failure(request, 1)

        assert response.status_code == 302
        assert "login" in response.url

    def test_staff_who_are_not_superusers_redirect_to_login(self):
        request = RequestFactory().post("/allotment-failure-acknowledge/1/")
        request.user = MagicMock(is_authenticated=True, is_superuser=False, is_staff=True)

        response = acknowledge_allotment_failure(request, 1)

        assert response.status_code == 302
        assert "login" in response.url

    def test_get_not_allowed(self):
        request = RequestFactory().get("/allotment-failure-acknowledge/1/")
        request.user = MagicMock(is_authenticated=True, is_superuser=True)

        assert acknowledge_allotment_failure(request, 1).status_code == 405


@pytest.mark.django_db
@pytest.mark.urls("tufts_local.tests.urls")
class TestAcknowledging:
    def test_resolves_the_failure_with_who_and_why(self, project):
        failure = make_failure(make_allocation(project))
        superuser = make_account("root", is_superuser=True)

        response = post(acknowledge_allotment_failure, failure.pk, superuser, reason="applied by hand", next="/alerts/")

        failure.refresh_from_db()
        assert failure.resolved_by == superuser
        assert failure.resolution_note == "applied by hand"
        assert response.status_code == 302
        assert response.url == "/alerts/"
        assert billing_alerts.allotment_failure_alerts() == []

    def test_the_alert_goes_at_once_for_whoever_acknowledged_it(self, project, monkeypatch):
        failure = make_failure(make_allocation(project))
        superuser = make_account("root", is_superuser=True)
        forgotten = []
        monkeypatch.setattr("tufts_local.views.billing_alerts.forget_current_alerts", lambda: forgotten.append(True))

        post(acknowledge_allotment_failure, failure.pk, superuser)

        assert forgotten == [True]

    def test_acknowledging_twice_keeps_the_first(self, project):
        failure = make_failure(make_allocation(project))
        first = make_account("root", is_superuser=True)
        second = make_account("root2", is_superuser=True)
        post(acknowledge_allotment_failure, failure.pk, first)

        post(acknowledge_allotment_failure, failure.pk, second)

        failure.refresh_from_db()
        assert failure.resolved_by == first

    def test_an_offsite_next_goes_to_the_alerts_list(self, project):
        failure = make_failure(make_allocation(project))

        response = post(
            acknowledge_allotment_failure,
            failure.pk,
            make_account("root", is_superuser=True),
            next="https://evil.example/",
        )

        assert response.url == reverse("alert-list")
