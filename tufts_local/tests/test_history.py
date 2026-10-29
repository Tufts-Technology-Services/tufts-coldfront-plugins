# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest
from django.contrib.auth import get_user_model

from coldfront.core.project.models import (
    AttributeType,
    Project,
    ProjectAttribute,
    ProjectAttributeType,
    ProjectStatusChoice,
    ProjectUser,
)

from tufts_local.history import SYSTEM, get_project_membership_changes


def make_person(username, first_name='', last_name=''):
    return get_user_model().objects.create(username=username, first_name=first_name, last_name=last_name)


def save_as(instance, user):
    """Save with `user` recorded as having made the change, as HistoryRequestMiddleware does in a request."""
    instance._history_user = user
    instance.save()


def add_member(project, username, role, status, by=None):
    member = ProjectUser(project=project, user=make_person(username), role=role, status=status)
    save_as(member, by)
    return member


def summary(changes):
    return [(change.username, change.action, change.role_before, change.role_after, change.who) for change in changes]


def status_summary(changes):
    return [(change.username, change.action, change.status_before, change.status_after) for change in changes]


@pytest.mark.django_db
class TestGetProjectMembershipChanges:
    def test_additions_with_the_pi_marked(self, project, roles, statuses):
        admin = make_person('rdms_admin')
        add_member(project, 'jdoe01', roles['User'], statuses['Active'], by=admin)

        changes = get_project_membership_changes(project.pk)

        assert summary(changes) == [
            ('jdoe01', 'added', '', 'User', 'rdms_admin'),
            ('pi01', 'added', '', 'Manager', SYSTEM),
        ]
        assert [change.is_pi for change in changes] == [False, True]
        assert changes[1].full_name == 'Pat Investigator'
        assert [change.status_after for change in changes] == ['Active', 'Active']

    def test_a_role_change_says_who_made_it(self, project, roles, statuses):
        admin = make_person('rdms_admin')
        member = add_member(project, 'jdoe01', roles['User'], statuses['Active'])

        member.role = roles['Manager']
        save_as(member, admin)

        assert summary(get_project_membership_changes(project.pk))[0] == (
            'jdoe01',
            'changed',
            'User',
            'Manager',
            'rdms_admin',
        )

    def test_removing_someone_is_a_status_change(self, project, roles, statuses):
        """ColdFront removes a member by setting their status to Removed, not by deleting the row."""
        admin = make_person('rdms_admin')
        member = add_member(project, 'jdoe01', roles['User'], statuses['Active'])

        member.status = statuses['Removed']
        save_as(member, admin)

        (change,) = [change for change in get_project_membership_changes(project.pk) if change.action == 'changed']
        assert (change.username, change.status_before, change.status_after, change.who) == (
            'jdoe01',
            'Active',
            'Removed',
            'rdms_admin',
        )
        # the role didn't change, so it isn't reported as changing
        assert (change.role_changed, change.role_before, change.role_after) == (False, '', '')

    def test_role_and_status_changed_in_one_save(self, project, roles, statuses):
        member = add_member(project, 'jdoe01', roles['User'], statuses['Active'])

        member.role = roles['Manager']
        member.status = statuses['Removed']
        member.save()

        change = get_project_membership_changes(project.pk)[0]
        assert (change.role_before, change.role_after, change.status_before, change.status_after) == (
            'User',
            'Manager',
            'Active',
            'Removed',
        )

    def test_saves_that_change_neither_are_left_out(self, project, roles, statuses):
        member = add_member(project, 'jdoe01', roles['User'], statuses['Active'])

        member.enable_notifications = False
        member.save()

        assert [
            change.action for change in get_project_membership_changes(project.pk) if change.username == 'jdoe01'
        ] == ['added']

    def test_a_deleted_membership(self, project, roles, statuses):
        member = add_member(project, 'jdoe01', roles['User'], statuses['Active'])

        member.delete()

        assert summary(get_project_membership_changes(project.pk))[0] == ('jdoe01', 'deleted', 'User', '', SYSTEM)
        assert status_summary(get_project_membership_changes(project.pk))[0] == ('jdoe01', 'deleted', 'Active', '')

    def test_newest_first_across_members(self, project, roles, statuses):
        first = add_member(project, 'jdoe01', roles['User'], statuses['Active'])
        add_member(project, 'asmith02', roles['User'], statuses['Active'])
        first.role = roles['Manager']
        first.save()

        assert [(change.username, change.action) for change in get_project_membership_changes(project.pk)] == [
            ('jdoe01', 'changed'),
            ('asmith02', 'added'),
            ('jdoe01', 'added'),
            ('pi01', 'added'),
        ]

    def test_history_that_starts_with_a_change_reads_as_added(self, project, roles, statuses):
        """Memberships older than the history have no creation row; their first known row stands in."""
        member = add_member(project, 'jdoe01', roles['User'], statuses['Active'])
        member.role = roles['Manager']
        member.save()
        ProjectUser.history.filter(id=member.pk, history_type='+').delete()

        assert [
            change.action for change in get_project_membership_changes(project.pk) if change.username == 'jdoe01'
        ] == ['added']

    def test_other_projects_are_left_out(self, project, roles, statuses):
        other = Project.objects.create(title='Other', pi=project.pi, status=project.status)
        add_member(other, 'jdoe01', roles['User'], statuses['Active'])

        assert [change.username for change in get_project_membership_changes(project.pk)] == ['pi01']

    def test_a_fixed_number_of_queries_however_long_the_history(
        self, project, roles, statuses, django_assert_num_queries
    ):
        """The project, the role and status names, and the history rows: never one query per row."""
        for index in range(5):
            member = add_member(project, f'user{index:02d}', roles['User'], statuses['Active'])
            member.role = roles['Manager']
            member.save()

        with django_assert_num_queries(4):
            changes = get_project_membership_changes(project.pk)

        assert len(changes) == 11

    def test_an_unknown_project_raises(self, db):
        with pytest.raises(Project.DoesNotExist):
            get_project_membership_changes(999999)


@pytest.fixture
def superuser_client(client):
    client.force_login(get_user_model().objects.create(username='rdms_admin', is_superuser=True))
    return client


def add_project_key(project, value):
    text = AttributeType.objects.get_or_create(name='Text')[0]
    key_type = ProjectAttributeType.objects.get_or_create(name='Project Key', attribute_type=text)[0]
    ProjectAttribute.objects.create(proj_attr_type=key_type, project=project, value=value)


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestHistoryAccess:
    @pytest.mark.parametrize('path', ['/history/', '/history/project/1/'])
    def test_staff_who_are_not_superusers_may_not(self, client, path):
        client.force_login(get_user_model().objects.create(username='staff01', is_staff=True))

        assert client.get(path).status_code == 302

    @pytest.mark.parametrize('path', ['/history/', '/history/project/1/'])
    def test_anonymous_users_may_not(self, client, path):
        response = client.get(path)

        assert response.status_code == 302
        assert 'login' in response.url


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestProjectHistorySearch:
    def found(self, client, query):
        return [project.title for project in client.get('/history/', {'q': query}).context['projects']]

    def test_by_title(self, superuser_client, project):
        assert self.found(superuser_client, 'ocean') == ['Ocean Modelling']

    def test_by_pi_username(self, superuser_client, project):
        assert self.found(superuser_client, 'pi01') == ['Ocean Modelling']

    def test_by_project_key(self, superuser_client, project):
        add_project_key(project, 'TTS-OCEAN-7')

        assert self.found(superuser_client, 'ocean-7') == ['Ocean Modelling']

    def test_archived_projects_are_included(self, superuser_client, project):
        project.status = ProjectStatusChoice.objects.get_or_create(name='Archived')[0]
        project.save()

        assert self.found(superuser_client, 'ocean') == ['Ocean Modelling']

    def test_links_to_the_timeline(self, superuser_client, project):
        content = superuser_client.get('/history/', {'q': 'ocean'}).content.decode()

        assert f'href="/history/project/{project.pk}/"' in content

    def test_no_matches(self, superuser_client, project):
        content = superuser_client.get('/history/', {'q': 'glaciers'}).content.decode()

        assert 'There are no projects matching "glaciers" to display.' in content

    def test_nothing_listed_before_a_search(self, superuser_client, project):
        response = superuser_client.get('/history/')

        assert response.context['projects'] == []
        assert 'There are no projects' not in response.content.decode()

    def test_a_broad_search_is_capped(self, superuser_client, project, monkeypatch):
        monkeypatch.setattr('tufts_local.views.history.MAX_SEARCH_RESULTS', 2)
        for index in range(3):
            Project.objects.create(title=f'Ocean {index}', pi=project.pi, status=project.status)

        response = superuser_client.get('/history/', {'q': 'ocean'})

        assert len(response.context['projects']) == 2
        assert 'More than 2 projects match' in response.content.decode()


@pytest.mark.django_db
@pytest.mark.urls('tufts_local.tests.urls')
class TestProjectHistoryPage:
    def test_lists_the_changes(self, superuser_client, project, roles, statuses):
        admin = get_user_model().objects.get(username='rdms_admin')
        member = add_member(project, 'jdoe01', roles['User'], statuses['Active'])
        member.role = roles['Manager']
        save_as(member, admin)

        content = superuser_client.get(f'/history/project/{project.pk}/').content.decode()

        assert 'Project History: Ocean Modelling' in content
        assert 'Role: User &rarr; Manager' in content
        assert 'Added as Manager (Active)' in content
        assert '<span class="badge text-bg-secondary">PI</span>' in content
        assert 'rdms_admin' in content

    def test_shows_a_removal(self, superuser_client, project, roles, statuses):
        member = add_member(project, 'jdoe01', roles['User'], statuses['Active'])
        member.status = statuses['Removed']
        member.save()

        content = superuser_client.get(f'/history/project/{project.pk}/').content.decode()

        assert 'Status: Active &rarr; Removed' in content
        assert 'Role:' not in content

    def test_an_unknown_project_is_a_404(self, superuser_client):
        assert superuser_client.get('/history/project/999999/').status_code == 404

    def test_pages_of_changes(self, superuser_client, project, roles, statuses, monkeypatch):
        monkeypatch.setattr('tufts_local.views.history.CHANGES_PER_PAGE', 2)
        for index in range(3):
            add_member(project, f'user{index:02d}', roles['User'], statuses['Active'])

        first = superuser_client.get(f'/history/project/{project.pk}/')
        last = superuser_client.get(f'/history/project/{project.pk}/', {'page': 2})

        assert first.context['page_obj'].paginator.num_pages == 2
        assert 'Page 1 of 2' in first.content.decode()
        assert [change.username for change in last.context['page_obj']] == ['user00', 'pi01']
