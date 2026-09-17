from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import AppSetting, Identity, Organization, Person, Project, Repository
from apps.catalog.setting_defs import SETTING_DEFS
from config.admin import ReadOnlyAdminMixin

_SETTING_DEFS_BY_KEY = {d.key: d for d in SETTING_DEFS}


@admin.register(Organization)
class OrganizationAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("login", "type", "is_active")
    list_filter = ("type", "is_active")
    search_fields = ("login",)


@admin.register(Repository)
class RepositoryAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("full_name", "organization", "connection", "is_active", "is_archived", "last_synced_at")
    list_filter = ("is_active", "is_archived", "is_private")
    list_select_related = ("organization", "connection")
    raw_id_fields = ("organization", "connection")
    search_fields = ("full_name",)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "color")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    filter_horizontal = ("repositories",)


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("display_name", "team", "role_hint", "is_active", "is_bot", "exclude_from_metrics")
    list_filter = ("is_active", "is_bot", "exclude_from_metrics", "role_hint")
    search_fields = ("display_name", "team")


@admin.register(Identity)
class IdentityAdmin(admin.ModelAdmin):
    list_display = ("value", "kind", "person", "first_seen_at")
    list_filter = ("kind",)
    list_select_related = ("person",)
    raw_id_fields = ("person",)
    search_fields = ("value",)


@admin.register(AppSetting)
class AppSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "value_type", "value", "registry_description")
    list_filter = ("value_type",)
    search_fields = ("key",)
    readonly_fields = ("key", "value_type")

    @admin.display(description=_("description"))
    def registry_description(self, obj: AppSetting) -> str:
        setting_def = _SETTING_DEFS_BY_KEY.get(obj.key)
        return str(setting_def.description) if setting_def is not None else ""
