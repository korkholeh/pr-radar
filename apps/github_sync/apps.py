from django.apps import AppConfig


class GithubSyncConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.github_sync"
    label = "github_sync"
