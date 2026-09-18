from django.contrib import admin

from apps.metrics.models import DailyRollup, DataVersion, DirtyDay
from config.admin import ReadOnlyAdminMixin


@admin.register(DailyRollup)
class DailyRollupAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("date", "scope_type", "scope_id", "cohort", "metric_key", "value", "sample_size")
    list_filter = ("scope_type", "cohort", "metric_key")


@admin.register(DataVersion)
class DataVersionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "version", "updated_at")


@admin.register(DirtyDay)
class DirtyDayAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("date", "marked_at")
