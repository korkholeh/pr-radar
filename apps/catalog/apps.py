from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.catalog"
    label = "catalog"

    def ready(self) -> None:
        from django.db.models.signals import post_delete, post_save

        from apps.catalog.models import AppSetting
        from apps.catalog.services import invalidate_settings_cache

        post_save.connect(invalidate_settings_cache, sender=AppSetting)
        post_delete.connect(invalidate_settings_cache, sender=AppSetting)
