# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest.mock import MagicMock, patch

from tufts_local.starfish_utils import set_project_approvers


class FakeProjectUserQuerySet(list):
    def values_list(self, *args, **kwargs):
        return [(pu.user.username,) for pu in self]


def make_proj_user(user, role, status):
    proj_user = MagicMock()
    proj_user.user = user
    proj_user.role = role
    proj_user.status = status
    return proj_user


@patch("tufts_local.starfish_utils.create_user")
@patch("tufts_local.starfish_utils.ProjectUserStatusChoice")
@patch("tufts_local.starfish_utils.ProjectUserRoleChoice")
class TestSetProjectApproversPreservesPiRole:
    def _setup_roles(self, mock_role_choice, mock_status_choice):
        manager_role = MagicMock(name="manager_role")
        member_role = MagicMock(name="member_role")
        active_status = MagicMock(name="active_status")
        mock_role_choice.objects.get.side_effect = lambda name: {
            "Manager": manager_role,
            "User": member_role,
        }[name]
        mock_status_choice.objects.get.return_value = active_status
        return manager_role, member_role, active_status

    def test_pi_role_not_downgraded_when_pi_is_not_a_starfish_approver(
        self, mock_role_choice, mock_status_choice, mock_create_user
    ):
        manager_role, member_role, active_status = self._setup_roles(mock_role_choice, mock_status_choice)

        pi_user = MagicMock(username="pi_user")
        proj = MagicMock()
        proj.pi = pi_user
        pi_proj_user = make_proj_user(pi_user, manager_role, active_status)
        proj.projectuser_set.all.return_value = FakeProjectUserQuerySet([pi_proj_user])

        # the PI is not listed as an approver in starfish, which previously caused
        # the job to demote the PI from Manager to User
        # someone_else isn't a member, so they're added as one
        with patch("tufts_local.starfish_utils.ProjectUser") as mock_project_user:
            mock_project_user.objects.get_or_create.return_value = (MagicMock(), True)
            set_project_approvers(proj, {"someone_else"})

        assert pi_proj_user.role == manager_role
        pi_proj_user.save.assert_not_called()

    def test_pi_role_not_downgraded_when_pi_is_a_starfish_approver(
        self, mock_role_choice, mock_status_choice, mock_create_user
    ):
        manager_role, member_role, active_status = self._setup_roles(mock_role_choice, mock_status_choice)

        pi_user = MagicMock(username="pi_user")
        proj = MagicMock()
        proj.pi = pi_user
        pi_proj_user = make_proj_user(pi_user, manager_role, active_status)
        proj.projectuser_set.all.return_value = FakeProjectUserQuerySet([pi_proj_user])

        set_project_approvers(proj, {"pi_user"})

        assert pi_proj_user.role == manager_role
        pi_proj_user.save.assert_not_called()

    def test_non_pi_users_are_still_updated_normally(self, mock_role_choice, mock_status_choice, mock_create_user):
        manager_role, member_role, active_status = self._setup_roles(mock_role_choice, mock_status_choice)

        pi_user = MagicMock(username="pi_user")
        approver_user = MagicMock(username="approver_user")
        proj = MagicMock()
        proj.pi = pi_user
        pi_proj_user = make_proj_user(pi_user, manager_role, active_status)
        approver_proj_user = make_proj_user(approver_user, member_role, active_status)
        proj.projectuser_set.all.return_value = FakeProjectUserQuerySet([pi_proj_user, approver_proj_user])

        set_project_approvers(proj, {"approver_user"})

        assert pi_proj_user.role == manager_role
        pi_proj_user.save.assert_not_called()
        assert approver_proj_user.role == manager_role
        approver_proj_user.save.assert_called_once()
