# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest.mock import MagicMock, patch

from tufts_local.starfish_utils import set_project_approvers_from_starfish


class FakeProjectUserQuerySet(list):
    def values_list(self, *args, **kwargs):
        return [(pu.user.username,) for pu in self]


def make_proj_user(user, role, status):
    proj_user = MagicMock()
    proj_user.user = user
    proj_user.role = role
    proj_user.status = status
    return proj_user


def make_vol_path_data(vol_path='projects:myproj', approvers=None):
    tags = f'Approver:{",".join(approvers)}' if approvers else ''
    return {'vol_path': vol_path, 'tags_explicit': tags}


@patch('tufts_local.starfish_utils.create_user')
@patch('tufts_local.starfish_utils.ProjectUserStatusChoice')
@patch('tufts_local.starfish_utils.ProjectUserRoleChoice')
@patch('tufts_local.starfish_utils.Allocation')
@patch('tufts_local.starfish_utils.get_project_by_key')
class TestSetProjectApproversFromStarfishPreservesPiRole:
    def _setup_roles(self, mock_role_choice, mock_status_choice):
        manager_role = MagicMock(name='manager_role')
        member_role = MagicMock(name='member_role')
        active_status = MagicMock(name='active_status')
        mock_role_choice.objects.get.side_effect = lambda name: {
            'Manager': manager_role,
            'User': member_role,
        }[name]
        mock_status_choice.objects.get.return_value = active_status
        return manager_role, member_role, active_status

    def test_pi_role_not_downgraded_when_pi_is_not_a_starfish_approver(
        self, mock_get_project_by_key, mock_allocation, mock_role_choice, mock_status_choice, mock_create_user
    ):
        manager_role, member_role, active_status = self._setup_roles(mock_role_choice, mock_status_choice)
        mock_allocation.objects.filter.return_value.exists.return_value = False

        pi_user = MagicMock(username='pi_user')
        proj = MagicMock()
        proj.pi = pi_user
        pi_proj_user = make_proj_user(pi_user, manager_role, active_status)
        proj.projectuser_set.all.return_value = FakeProjectUserQuerySet([pi_proj_user])
        mock_get_project_by_key.return_value = proj

        # the PI is not listed as an approver in starfish, which previously caused
        # the job to demote the PI from Manager to User
        vol_path_data = make_vol_path_data(approvers=['someone_else'])

        set_project_approvers_from_starfish(vol_path_data)

        assert pi_proj_user.role == manager_role
        pi_proj_user.save.assert_not_called()

    def test_pi_role_not_downgraded_when_pi_is_a_starfish_approver(
        self, mock_get_project_by_key, mock_allocation, mock_role_choice, mock_status_choice, mock_create_user
    ):
        manager_role, member_role, active_status = self._setup_roles(mock_role_choice, mock_status_choice)
        mock_allocation.objects.filter.return_value.exists.return_value = False

        pi_user = MagicMock(username='pi_user')
        proj = MagicMock()
        proj.pi = pi_user
        pi_proj_user = make_proj_user(pi_user, manager_role, active_status)
        proj.projectuser_set.all.return_value = FakeProjectUserQuerySet([pi_proj_user])
        mock_get_project_by_key.return_value = proj

        vol_path_data = make_vol_path_data(approvers=['pi_user'])

        set_project_approvers_from_starfish(vol_path_data)

        assert pi_proj_user.role == manager_role
        pi_proj_user.save.assert_not_called()

    def test_non_pi_users_are_still_updated_normally(
        self, mock_get_project_by_key, mock_allocation, mock_role_choice, mock_status_choice, mock_create_user
    ):
        manager_role, member_role, active_status = self._setup_roles(mock_role_choice, mock_status_choice)
        mock_allocation.objects.filter.return_value.exists.return_value = False

        pi_user = MagicMock(username='pi_user')
        approver_user = MagicMock(username='approver_user')
        proj = MagicMock()
        proj.pi = pi_user
        pi_proj_user = make_proj_user(pi_user, manager_role, active_status)
        approver_proj_user = make_proj_user(approver_user, member_role, active_status)
        proj.projectuser_set.all.return_value = FakeProjectUserQuerySet([pi_proj_user, approver_proj_user])
        mock_get_project_by_key.return_value = proj

        vol_path_data = make_vol_path_data(approvers=['approver_user'])

        set_project_approvers_from_starfish(vol_path_data)

        assert pi_proj_user.role == manager_role
        pi_proj_user.save.assert_not_called()
        assert approver_proj_user.role == manager_role
        approver_proj_user.save.assert_called_once()
