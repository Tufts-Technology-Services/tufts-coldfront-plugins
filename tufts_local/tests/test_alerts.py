# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission

from tufts_local import alerts
from tufts_local.alerts import (
    Alert,
    collect_alerts,
    get_current_alerts,
    register_alert_source,
    staff,
    superusers,
    with_permission,
)
from tufts_local.tests.test_task_summary import add_session, make_user
from tufts_local.views import alert_indicator, alert_list

STAFF = make_user(is_staff=True)


def make_account(username, **flags):
    return get_user_model().objects.create(username=username, **flags)


def grant(user, perm):
    app_label, codename = perm.split('.')
    user.user_permissions.add(Permission.objects.get(content_type__app_label=app_label, codename=codename))
    # has_perm caches what it finds on the instance, so read the user afresh
    return get_user_model().objects.get(pk=user.pk)


@pytest.fixture
def no_sources(monkeypatch):
    """An empty registry, so a test sees only the sources it registers itself."""
    monkeypatch.setattr(alerts, '_alert_sources', {})


@pytest.mark.usefixtures('no_sources')
class TestCollectAlerts:
    def test_nothing_registered_means_no_alerts(self):
        assert collect_alerts() == []

    def test_worst_first_across_sources(self):
        register_alert_source('a')(lambda: [Alert('info', 'fyi'), Alert('warning', 'hmm')])
        register_alert_source('b')(lambda: [Alert('danger', 'down')])

        assert [alert.title for alert in collect_alerts()] == ['down', 'hmm', 'fyi']

    def test_each_alert_is_labelled_with_its_source(self):
        register_alert_source('task queue')(lambda: [Alert('danger', 'down')])

        assert collect_alerts()[0].source == 'task queue'

    def test_a_failing_source_becomes_a_warning(self):
        def broken():
            raise RuntimeError('starfish timed out')

        register_alert_source('starfish')(broken)
        register_alert_source('fine')(lambda: [Alert('info', 'still here')])

        found = collect_alerts()

        assert [alert.title for alert in found] == ['Could not check starfish', 'still here']
        assert found[0].severity == 'warning'
        assert 'RuntimeError: starfish timed out' in found[0].detail

    @pytest.mark.parametrize('returned, error', [([None], 'TypeError: expected'), (None, 'TypeError')])
    def test_a_source_returning_the_wrong_thing_becomes_a_warning(self, returned, error):
        register_alert_source('sloppy')(lambda: returned)
        register_alert_source('fine')(lambda: [Alert('info', 'still here')])

        found = collect_alerts()

        assert [alert.title for alert in found] == ['Could not check sloppy', 'still here']
        assert found[0].source == 'sloppy'
        assert found[0].detail.startswith(error)

    def test_names_are_unique(self):
        register_alert_source('a')(lambda: [])

        with pytest.raises(ValueError):
            register_alert_source('a')(lambda: [])

    def test_reregistering_the_same_function_is_harmless(self):
        """A module imported twice registers the same function twice; that isn't a clash."""

        def source():
            return []

        register_alert_source('a')(source)
        register_alert_source('a')(source)


@pytest.mark.usefixtures('no_sources')
class TestGetCurrentAlerts:
    @pytest.fixture
    def counted_source(self):
        """A source that counts its runs, raising an alert only on its first."""
        runs = []

        def source():
            runs.append(1)
            return [Alert('danger', 'down')] if len(runs) == 1 else []

        register_alert_source('counted')(source)
        return runs

    def test_polls_within_the_interval_share_one_run(self, counted_source):
        first, first_checked = get_current_alerts(STAFF)
        second, second_checked = get_current_alerts(STAFF)

        assert len(counted_source) == 1
        assert [alert.title for alert in second] == ['down']
        assert second_checked == first_checked

    def test_sources_run_again_once_the_interval_is_up(self, counted_source, monkeypatch):
        monkeypatch.setattr(alerts, 'CACHE_SECONDS', 0)

        get_current_alerts(STAFF)
        found, _ = get_current_alerts(STAFF)

        assert len(counted_source) == 2
        assert found == []

    def test_callers_get_their_own_list(self, counted_source):
        found, _ = get_current_alerts(STAFF)
        found.clear()

        assert get_current_alerts(STAFF)[0] != []


@pytest.mark.django_db
@pytest.mark.usefixtures('no_sources')
class TestAudiences:
    REVIEW = 'allocation.can_review_allocation_requests'

    @pytest.fixture
    def people(self):
        return {
            'member': make_account('member01'),
            'staff': make_account('staff01', is_staff=True),
            'superuser': make_account('admin01', is_superuser=True),
            'reviewer': grant(make_account('reviewer01'), self.REVIEW),
        }

    def visible_to(self, people):
        return {name: [alert.title for alert in get_current_alerts(user)[0]] for name, user in people.items()}

    def test_staff_is_the_default(self, people):
        register_alert_source('plain')(lambda: [Alert('info', 'fyi')])

        assert self.visible_to(people) == {'member': [], 'staff': ['fyi'], 'superuser': ['fyi'], 'reviewer': []}

    def test_superusers_only(self, people):
        register_alert_source('root', audience=superusers)(lambda: [Alert('info', 'fyi')])

        assert self.visible_to(people) == {'member': [], 'staff': [], 'superuser': ['fyi'], 'reviewer': []}

    def test_with_permission_includes_superusers_but_not_other_staff(self, people):
        register_alert_source('review', audience=with_permission(self.REVIEW))(lambda: [Alert('info', 'fyi')])

        assert self.visible_to(people) == {'member': [], 'staff': [], 'superuser': ['fyi'], 'reviewer': ['fyi']}

    def test_with_permission_excludes_inactive_holders(self, people):
        register_alert_source('review', audience=with_permission(self.REVIEW))(lambda: [Alert('info', 'fyi')])
        people['reviewer'].is_active = False
        people['reviewer'].save()

        assert get_current_alerts(get_user_model().objects.get(pk=people['reviewer'].pk))[0] == []

    def test_each_reader_gets_only_their_sources(self, people):
        register_alert_source('staff', audience=staff)(lambda: [Alert('warning', 'for staff')])
        register_alert_source('review', audience=with_permission(self.REVIEW))(lambda: [Alert('danger', 'to review')])

        assert self.visible_to(people) == {
            'member': [],
            'staff': ['for staff'],
            'superuser': ['to review', 'for staff'],
            'reviewer': ['to review'],
        }

    def test_a_failure_warning_goes_to_the_source_audience_only(self, people):
        def broken():
            raise RuntimeError('redis-host:6379 refused')

        register_alert_source('review', audience=with_permission(self.REVIEW))(broken)

        seen = self.visible_to(people)

        assert seen['reviewer'] == ['Could not check review']
        assert seen['staff'] == []

    def test_readers_with_different_audiences_share_one_run(self, people):
        runs = []

        def source():
            runs.append(1)
            return [Alert('info', 'fyi')]

        register_alert_source('root', audience=superusers)(source)

        self.visible_to(people)

        assert len(runs) == 1


def test_unknown_severity_is_rejected():
    """The severity doubles as the badge colour, so a typo would otherwise render grey and silent."""
    with pytest.raises(ValueError):
        Alert('critical', 'down')


def get_alert_view(rf, view, path, user=None):
    request = rf.get(path)
    request.user = user or make_user(is_staff=True)
    add_session(request)
    response = view(request)
    if hasattr(response, 'render'):
        response.render()
    return response


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestAlertViews:
    @pytest.mark.parametrize('view, path', [(alert_list, '/alerts/'), (alert_indicator, '/alert-indicator/')])
    def test_ordinary_users_may_not_see_them(self, rf, view, path):
        response = get_alert_view(rf, view, path, user=make_user())

        assert response.status_code == 302

    @pytest.mark.parametrize('view, path', [(alert_list, '/alerts/'), (alert_indicator, '/alert-indicator/')])
    def test_anonymous_users_may_not_see_them(self, rf, view, path):
        request = rf.get(path)
        request.user = AnonymousUser()

        assert view(request).status_code == 302

    def test_indicator_is_a_bare_bell_when_all_is_well(self, rf, fake_broker):
        fake_broker.add_cluster()

        content = get_alert_view(rf, alert_indicator, '/alert-indicator/').content.decode()

        assert 'No alerts' in content
        assert 'opacity-50' in content
        assert 'rounded-circle' not in content

    def test_indicator_counts_a_queue_outage_in_red(self, rf, fake_broker):
        fake_broker.reachable = False

        content = get_alert_view(rf, alert_indicator, '/alert-indicator/').content.decode()

        assert 'bg-danger border border-light rounded-circle' in content
        assert '<span class="fw-semibold">1 alert</span>' in content
        assert '<small class="opacity-75">&middot; 1 critical</small>' in content
        assert 'Task queue: broker down' in content

    def test_indicator_shows_one_dot_for_the_worst_alert(self, rf, no_sources):
        register_alert_source('mixed')(lambda: [Alert('info', 'fyi'), Alert('danger', 'down'), Alert('danger', 'also')])

        content = get_alert_view(rf, alert_indicator, '/alert-indicator/').content.decode()

        assert content.count('rounded-circle') == 1
        assert 'bg-danger border' in content
        assert '<span class="fw-semibold">3 alerts</span>' in content
        assert '&middot; 2 critical' in content

    def test_indicator_has_no_secondary_text_without_critical_alerts(self, rf, no_sources):
        register_alert_source('mild')(lambda: [Alert('warning', 'hmm'), Alert('info', 'fyi')])

        content = get_alert_view(rf, alert_indicator, '/alert-indicator/').content.decode()

        assert 'bg-warning border' in content
        assert '<span class="fw-semibold">2 alerts</span>' in content
        assert 'critical' not in content

    def test_list_shows_the_alert_with_its_link(self, rf):
        """The autouse fake broker has no clusters, which is the 'no workers' alert."""
        content = get_alert_view(rf, alert_list, '/alerts/').content.decode()

        assert 'Task queue: no workers' in content
        assert 'href="/task-summary/"' in content
        assert '<span class="badge text-bg-danger">Critical</span>' in content

    def test_list_offers_acknowledge_only_where_the_source_does(self, rf, no_sources):
        register_alert_source('ackable')(
            lambda: [Alert('danger', 'tasks failed', ack_url='/task-acknowledge-bulk/?before=x')]
        )
        register_alert_source('condition')(lambda: [Alert('danger', 'broker down')])

        content = get_alert_view(rf, alert_list, '/alerts/').content.decode()

        assert content.count('>Acknowledge</button>') == 1
        assert 'action="/task-acknowledge-bulk/?before=x"' in content
        assert 'name="next" value="/alerts/"' in content

    def test_list_empty_state(self, rf, fake_broker):
        fake_broker.add_cluster()

        content = get_alert_view(rf, alert_list, '/alerts/').content.decode()

        assert 'There are no alerts to display.' in content
