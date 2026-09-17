from django.apps import apps

EXPECTED_LABELS = {
    "accounts",
    "connections",
    "catalog",
    "github_sync",
    "activity",
    "ai_detection",
    "policy",
    "metrics",
    "churn",
    "dashboards",
}


def test_every_component_app_is_installed():
    installed_labels = {config.label for config in apps.get_app_configs()}
    assert EXPECTED_LABELS <= installed_labels


def test_every_component_app_name_starts_with_apps():
    for label in EXPECTED_LABELS:
        config = apps.get_app_config(label)
        assert config.name.startswith("apps.")
