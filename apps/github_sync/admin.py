from django.contrib import admin

from apps.github_sync.models import SyncRun
from config.admin import ReadOnlyAdminMixin


@admin.register(SyncRun)
class SyncRunAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("started_at", "finished_at", "trigger", "status")
    list_filter = ("trigger", "status")
