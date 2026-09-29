# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from django.contrib import admin

from tufts_local.models import IgnoredTask, TaskAcknowledgement


@admin.register(IgnoredTask)
class IgnoredTaskAdmin(admin.ModelAdmin):
    list_display = ('field', 'pattern', 'reason', 'created')
    list_filter = ('field',)
    search_fields = ('pattern', 'reason')


@admin.register(TaskAcknowledgement)
class TaskAcknowledgementAdmin(admin.ModelAdmin):
    list_display = ('task_id', 'task_name', 'acknowledged_by', 'acknowledged_at', 'reason')
    list_filter = ('acknowledged_at', 'acknowledged_by')
    search_fields = ('task__id', 'task__name', 'reason')
    # the task picker would otherwise render every row in django-q's table
    raw_id_fields = ('task',)
    readonly_fields = ('acknowledged_at',)
    list_select_related = ('task', 'acknowledged_by')

    @admin.display(description='Task name', ordering='task__name')
    def task_name(self, acknowledgement):
        return acknowledgement.task.name
