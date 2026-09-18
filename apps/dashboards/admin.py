from django.contrib import admin

from apps.dashboards.models import ExportJob
from config.admin import ReadOnlyAdminMixin


@admin.register(ExportJob)
class ExportJobAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("__str__", "user", "kind", "status", "created_at", "expires_at")
    list_filter = ("kind", "status")
    list_select_related = ("user",)
    raw_id_fields = ("user",)
