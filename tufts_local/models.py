"""
The one exception to this app's no-custom-models rule (see claude.md): an ignore list for
the django-q task report. It is plugin-local operational config that maps to no coldfront
entity, and administrators maintain it through the Django admin.
"""

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
