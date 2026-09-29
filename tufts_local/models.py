# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The exceptions to this app's no-custom-models rule.

Custom data normally hangs off coldfront's own models through its attribute system, and
anything living outside coldfront's schema is reached through a thin client module rather
than modelled here. Both models below are the same kind of exception: plugin-local
operational state about the django-q task queue, which maps to no coldfront entity and has
nowhere in django-q's own third-party tables to live. Administrators maintain both through
the Django admin. A third model needs the same kind of justification.
"""

from django.conf import settings
from django.db import models


class IgnoredTask(models.Model):
    NAME = 'name'
    GROUP = 'group'
    FIELD_CHOICES = (
        (NAME, 'Task name (starts with)'),
        (GROUP, 'Group id (exact)'),
    )

    field = models.CharField(
        max_length=5,
        choices=FIELD_CHOICES,
        default=NAME,
        help_text='Which part of the task to match against.',
    )
    pattern = models.CharField(
        max_length=100,
        help_text=(
            'Task names match on a prefix, so add_sf_tags hides every '
            'add_sf_tags_alloc_activate_<id> task. Group ids must match in full. '
            'Matching ignores case.'
        ),
    )
    reason = models.CharField(
        max_length=255,
        blank=True,
        help_text='Optional note about why these tasks are hidden.',
    )
    created = models.DateTimeField(auto_now_add=True)

    def clean(self):
        self.pattern = (self.pattern or '').strip()

    def __str__(self):
        return f'{self.get_field_display()}: {self.pattern}'

    class Meta:
        ordering = ['field', 'pattern']
        unique_together = [('field', 'pattern')]
        verbose_name = 'ignored task'
        verbose_name_plural = 'ignored tasks'


class TaskAcknowledgement(models.Model):
    """
    A record that someone has seen a failed task and dealt with it.

    Acknowledged failures stop counting towards the background-task indicator, so the
    badge goes back to meaning 'something needs looking at' rather than 'the queue has a
    history'. Deleting the acknowledgement lights it again.

    Not to be confused with IgnoredTask, which is a standing pattern rule that hides whole
    classes of task from the report forever. This is one-shot, about one task that has
    already run, and it leaves the row on the page.
    """

    task = models.OneToOneField(
        'django_q.Task',
        on_delete=models.CASCADE,
        related_name='acknowledgement',
        help_text='The failed task being acknowledged.',
    )
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        # keep the record when the account goes away: who acknowledged it is history, and
        # a PROTECT here would block deleting the user instead
        on_delete=models.SET_NULL,
        related_name='+',
    )
    acknowledged_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(
        max_length=255,
        blank=True,
        help_text='Optional note about why this failure is nothing to worry about.',
    )

    def __str__(self):
        return f'{self.task_id} acknowledged by {self.acknowledged_by or "a deleted user"}'

    class Meta:
        ordering = ['-acknowledged_at']
        verbose_name = 'task acknowledgement'
        verbose_name_plural = 'task acknowledgements'
