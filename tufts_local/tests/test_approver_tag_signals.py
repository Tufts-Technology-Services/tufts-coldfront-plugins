# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Any change to who a project's approvers are queues the update of its Starfish Approver tags.

Only the approver toggle on the project page used to queue it. A Manager added through Add Users,
or a role changed on ColdFront's project-user page or in the admin, never reached Starfish, so the
nightly read-back of the tags demoted them again (lplace02 on kaplanlab).
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from coldfront.core.project.models import ProjectUser

from tufts_local.starfish_utils import roles_from_starfish
from tufts_local.tasks import update_sf_approver_tags

User = get_user_model()


@pytest.fixture
def queued():
    with patch("tufts_local.signals.async_task") as async_task:
        yield async_task


@pytest.fixture
def committed():
    """Run what's left for after the commit straight away: the test's transaction never commits."""
    with patch("tufts_local.signals.transaction.on_commit", side_effect=lambda callback: callback()):
        yield


def add_member(project, username, role, status):
    return ProjectUser.objects.create(
        project=project, user=User.objects.create(username=username), role=role, status=status
    )


def assert_queued_for(queued, project):
    queued.assert_called_once()
    assert queued.call_args.args == (update_sf_approver_tags, project.pk)
    assert queued.call_args.kwargs["q_options"] == {"task_name": f"sf_approver_tags_{project.pk}", "group": "starfish"}


def test_adding_a_manager_queues_the_tags(project, roles, statuses, queued, committed):
    add_member(project, "approver01", roles["Manager"], statuses["Active"])

    assert_queued_for(queued, project)


def test_adding_a_user_queues_nothing(project, roles, statuses, queued, committed):
    add_member(project, "member01", roles["User"], statuses["Active"])

    queued.assert_not_called()


def test_making_a_user_a_manager_queues_the_tags(project, roles, statuses, queued, committed):
    member = add_member(project, "member01", roles["User"], statuses["Active"])

    member.role = roles["Manager"]
    member.save()

    assert_queued_for(queued, project)


def test_making_a_manager_a_user_queues_the_tags(project, roles, statuses, queued, committed):
    manager = add_member(project, "approver01", roles["Manager"], statuses["Active"])
    queued.reset_mock()

    manager.role = roles["User"]
    manager.save()

    assert_queued_for(queued, project)


def test_removing_a_manager_queues_the_tags(project, roles, statuses, queued, committed):
    manager = add_member(project, "approver01", roles["Manager"], statuses["Active"])
    queued.reset_mock()

    manager.status = statuses["Removed"]
    manager.save()

    assert_queued_for(queued, project)


def test_a_save_that_leaves_the_approvers_as_they_were_queues_nothing(project, roles, statuses, queued, committed):
    manager = add_member(project, "approver01", roles["Manager"], statuses["Active"])
    queued.reset_mock()

    manager.enable_notifications = not manager.enable_notifications
    manager.save()

    queued.assert_not_called()


def test_the_pis_own_membership_queues_nothing(project, roles, queued, committed):
    pi_member = ProjectUser.objects.get(project=project, user=project.pi)

    pi_member.role = roles["User"]
    pi_member.save()

    queued.assert_not_called()


def test_roles_set_from_starfish_queue_nothing(project, roles, statuses, queued, committed):
    manager = add_member(project, "approver01", roles["Manager"], statuses["Active"])
    queued.reset_mock()

    with roles_from_starfish():
        manager.role = roles["User"]
        manager.save()

    queued.assert_not_called()


def test_with_tags_off_nothing_is_queued(project, roles, statuses, queued, committed):
    with patch("tufts_local.signals.SF_OWNER_TAG_PERSIST", False):
        add_member(project, "approver01", roles["Manager"], statuses["Active"])

    queued.assert_not_called()


def test_a_failure_to_queue_doesnt_undo_the_change(project, roles, statuses, queued, committed, caplog):
    queued.side_effect = ConnectionError("broker unreachable")

    add_member(project, "approver01", roles["Manager"], statuses["Active"])

    assert ProjectUser.objects.filter(project=project, user__username="approver01").exists()
    assert "Could not queue" in caplog.text


def test_nothing_is_queued_until_the_change_is_committed(project, roles, statuses, queued):
    add_member(project, "approver01", roles["Manager"], statuses["Active"])

    queued.assert_not_called()
