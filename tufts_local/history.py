# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Readable change history from django-simple-history's tables, for the history views.

Pure data: nothing here renders. History rows are read in one ordered query per model and
compared with the previous row in Python, rather than through each row's prev_record, which
would cost a query per row.
"""

import datetime
from dataclasses import dataclass

from coldfront.core.project.models import Project, ProjectUser, ProjectUserRoleChoice, ProjectUserStatusChoice

# who made a change that has no user recorded: a django-q task, a signal or a shell session
SYSTEM = 'System'


@dataclass(frozen=True)
class MembershipChange:
    """One change to a project member's role, status or both."""

    when: datetime.datetime
    # who made the change: a username, or SYSTEM
    who: str
    # whose membership changed
    username: str
    full_name: str
    # is this the project's PI? The current PI, since a project's PI can change too
    is_pi: bool
    # 'added', 'changed' or 'deleted'. Removing someone from a project in ColdFront is a change
    # (to the Removed status); 'deleted' is the rarer deletion of the membership row itself.
    action: str
    # names either side of the change. For 'added' only the after values are set, for 'deleted'
    # only the before ones, and for 'changed' only those of whichever of the two changed.
    role_before: str = ''
    role_after: str = ''
    status_before: str = ''
    status_after: str = ''

    @property
    def role_changed(self):
        return self.role_before != self.role_after

    @property
    def status_changed(self):
        return self.status_before != self.status_after


def get_project_membership_changes(project_id):
    """
    Every change to the roles and statuses of a project's members, newest first.

    A membership's first history row counts as 'added' and a deleted row as 'deleted'. Any other
    row counts if its role or status differs from the row before it; saves that change neither,
    such as one that only touches `modified` or `enable_notifications`, are left out.

    Only saves are recorded: a QuerySet.update() or bulk_create bypasses django-simple-history.
    Raises Project.DoesNotExist for an unknown project_id.
    """
    project = Project.objects.only('pi_id').get(pk=project_id)
    role_names = dict(ProjectUserRoleChoice.objects.values_list('id', 'name'))
    status_names = dict(ProjectUserStatusChoice.objects.values_list('id', 'name'))
    # oldest first within each membership (`id` is the ProjectUser's pk), so each row can be
    # compared with the one before it
    records = (
        ProjectUser.history.filter(project_id=project_id)
        .select_related('user', 'history_user')
        .order_by('id', 'history_date', 'history_id')
    )

    changes = []
    # each membership's (role id, status id) as of its previous history row
    previous = {}
    for record in records:
        current = (record.role_id, record.status_id)
        before = previous.get(record.id)
        previous[record.id] = current
        role, status = role_names.get(record.role_id, ''), status_names.get(record.status_id, '')
        if record.history_type == '-':
            names = {'action': 'deleted', 'role_before': role, 'status_before': status}
        elif record.history_type == '+' or before is None:
            # a membership whose history starts with a change rather than a creation (its row
            # predates the history) is shown as added, the earliest that is known about it
            names = {'action': 'added', 'role_after': role, 'status_after': status}
        elif before != current:
            names = {'action': 'changed'}
            if before[0] != record.role_id:
                names.update(role_before=role_names.get(before[0], ''), role_after=role)
            if before[1] != record.status_id:
                names.update(status_before=status_names.get(before[1], ''), status_after=status)
        else:
            continue
        changes.append(
            MembershipChange(
                when=record.history_date,
                who=record.history_user.username if record.history_user else SYSTEM,
                username=record.user.username,
                full_name=record.user.get_full_name(),
                is_pi=record.user_id == project.pi_id,
                **names,
            )
        )
    changes.sort(key=lambda change: change.when, reverse=True)
    return changes
