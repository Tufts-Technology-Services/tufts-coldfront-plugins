# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import datetime

import pytest
from django.utils import timezone

from coldfront.core.allocation.models import Allocation, AllocationStatusChoice
from coldfront.core.project.models import ProjectReview, ProjectReviewStatusChoice

from tufts_local.alerts import get_current_alerts
from tufts_local.review_alerts import allocation_request_alerts, project_review_alerts, waiting_since
from tufts_local.tests.test_alerts import grant, make_account


def days_ago(days):
    return timezone.now() - datetime.timedelta(days=days)


def make_allocation(project, statuses):
    """
    An allocation that went through `statuses`, a list of (status name, days ago), oldest first.

    Each change is saved, so simple_history records it, then backdated: history rows and
    `created` both say when it really happened.
    """
    (first, first_days), *later = statuses
    allocation = Allocation.objects.create(
        project=project, status=AllocationStatusChoice.objects.get_or_create(name=first)[0], justification='research'
    )
    for name, _ in later:
        allocation.status = AllocationStatusChoice.objects.get_or_create(name=name)[0]
        allocation.save()
    history = Allocation.history.filter(id=allocation.pk).order_by('history_id')
    for row, (_, days) in zip(history, statuses, strict=True):
        Allocation.history.filter(history_id=row.history_id).update(history_date=days_ago(days))
    Allocation.objects.filter(pk=allocation.pk).update(created=days_ago(first_days))
    return allocation


def make_review(project, status):
    return ProjectReview.objects.create(
        project=project, status=ProjectReviewStatusChoice.objects.get_or_create(name=status)[0]
    )


@pytest.mark.django_db
class TestWaitingSince:
    def test_a_new_request_waits_from_when_it_was_made(self, project):
        allocation = make_allocation(project, [('New', 10)])

        assert (days_ago(10) - waiting_since()[allocation.pk]) < datetime.timedelta(minutes=1)

    def test_a_renewal_waits_from_when_it_was_requested(self, project):
        allocation = make_allocation(project, [('New', 400), ('Active', 399), ('Renewal Requested', 3)])

        assert (days_ago(3) - waiting_since()[allocation.pk]) < datetime.timedelta(minutes=1)

    def test_only_the_latest_renewal_counts(self, project):
        allocation = make_allocation(
            project,
            [('New', 800), ('Active', 799), ('Renewal Requested', 400), ('Active', 399), ('Renewal Requested', 2)],
        )

        assert (days_ago(2) - waiting_since()[allocation.pk]) < datetime.timedelta(minutes=1)

    def test_without_history_it_falls_back_to_created(self, project):
        allocation = make_allocation(project, [('New', 10)])
        Allocation.history.filter(id=allocation.pk).delete()

        assert (days_ago(10) - waiting_since()[allocation.pk]) < datetime.timedelta(minutes=1)

    @pytest.mark.parametrize('status', ['Active', 'Paid', 'Approved', 'Denied'])
    def test_allocations_not_awaiting_review_are_left_out(self, project, status):
        make_allocation(project, [(status, 30)])

        assert waiting_since() == {}


@pytest.mark.django_db
class TestAllocationRequestAlerts:
    def test_nothing_waiting_means_no_alert(self, project):
        make_allocation(project, [('New', 0.5)])

        assert allocation_request_alerts() == []

    def test_counts_requests_waiting_too_long(self, project):
        make_allocation(project, [('New', 12)])
        make_allocation(project, [('New', 400), ('Active', 399), ('Renewal Requested', 3)])
        make_allocation(project, [('New', 0.5)])

        [alert] = allocation_request_alerts()

        assert alert.severity == 'warning'
        assert alert.title == 'Allocation requests: 2 waiting more than 1 day'
        assert alert.detail == 'The oldest has waited 12 days.'
        assert alert.link == '/allocation/request-list'

    def test_a_recent_renewal_of_an_old_allocation_is_not_overdue(self, project):
        make_allocation(project, [('New', 400), ('Active', 399), ('Renewal Requested', 0.5)])

        assert allocation_request_alerts() == []

    def test_the_threshold_comes_from_settings(self, project, settings):
        settings.ALLOCATION_REQUEST_ALERT_DAYS = 7
        make_allocation(project, [('New', 2)])
        make_allocation(project, [('New', 8)])

        assert allocation_request_alerts()[0].title == 'Allocation requests: 1 waiting more than 7 days'

    def test_zero_days_alerts_on_every_waiting_request(self, project, settings):
        settings.ALLOCATION_REQUEST_ALERT_DAYS = 0
        make_allocation(project, [('New', 0)])
        make_allocation(project, [('New', 0.5)])

        [alert] = allocation_request_alerts()

        assert alert.title == 'Allocation requests: 2 waiting for review'
        assert alert.detail == 'The oldest has waited 0 days.'

    def test_zero_days_counts_a_request_made_this_moment(self, project, settings):
        """Not backdated at all: its history row and the check's cutoff can share a timestamp."""
        settings.ALLOCATION_REQUEST_ALERT_DAYS = 0
        Allocation.objects.create(
            project=project, status=AllocationStatusChoice.objects.get_or_create(name='New')[0], justification='x'
        )

        assert allocation_request_alerts()[0].title == 'Allocation requests: 1 waiting for review'


@pytest.mark.django_db
class TestProjectReviewAlerts:
    def test_nothing_pending_means_no_alert(self, project):
        make_review(project, 'Completed')

        assert project_review_alerts() == []

    def test_counts_pending_reviews(self, project):
        make_review(project, 'Pending')
        make_review(project, 'Pending')
        make_review(project, 'Completed')

        [alert] = project_review_alerts()

        assert alert.severity == 'info'
        assert alert.title == 'Project reviews: 2 pending'
        assert alert.link == '/project/project-review-list'


@pytest.mark.django_db
def test_review_alerts_go_to_reviewers_not_all_staff(project):
    """Through the real registry: the permission each source names is the one that decides."""
    make_allocation(project, [('New', 30)])
    make_review(project, 'Pending')
    allocation_reviewer = grant(make_account('alloc_reviewer'), 'allocation.can_review_allocation_requests')
    project_reviewer = grant(make_account('proj_reviewer'), 'project.can_review_pending_project_reviews')
    staff = make_account('staff01', is_staff=True)

    def sources_seen_by(user):
        return {alert.source for alert in get_current_alerts(user)[0]}

    assert sources_seen_by(allocation_reviewer) == {'allocation requests'}
    assert sources_seen_by(project_reviewer) == {'project reviews'}
    assert 'allocation requests' not in sources_seen_by(staff)
    assert 'project reviews' not in sources_seen_by(staff)
    assert {'allocation requests', 'project reviews'} <= sources_seen_by(make_account('admin01', is_superuser=True))
