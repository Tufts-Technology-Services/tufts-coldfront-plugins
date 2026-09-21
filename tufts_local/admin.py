from django.contrib import admin

from tufts_local.models import IgnoredTask


@admin.register(IgnoredTask)
class IgnoredTaskAdmin(admin.ModelAdmin):
    list_display = ('field', 'pattern', 'reason', 'created')
    list_filter = ('field',)
    search_fields = ('pattern', 'reason')
