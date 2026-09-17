from django.contrib import admin

from apps.accounts.models import AuditEntry, UserPreference, UserProjectAccess
from config.admin import ReadOnlyAdminMixin


@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "theme", "language")
    search_fields = ("user__username",)


@admin.register(AuditEntry)
class AuditEntryAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "object_type", "object_id")
    list_filter = ("action", "object_type")
    search_fields = ("object_id",)
    date_hierarchy = "created_at"


@admin.register(UserProjectAccess)
class UserProjectAccessAdmin(admin.ModelAdmin):
    list_display = ("user", "project", "created_at")
    list_select_related = ("user", "project")
    raw_id_fields = ("user", "project")
