from django.contrib import admin

from apps.churn.models import ChurnResult
from config.admin import ReadOnlyAdminMixin


@admin.register(ChurnResult)
class ChurnResultAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("pull_request", "window_days", "status", "churn_ratio", "computed_at")
    list_filter = ("status", "window_days")
    list_select_related = ("pull_request__repository",)
