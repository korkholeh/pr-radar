# Generated for phase 9 — adds the PR detail file cap and reviews heat map settings to AppSetting.

from django.db import migrations

from apps.catalog.setting_defs import SETTING_DEFS

_NEW_KEYS = {
    "PR_FILES_DISPLAY_LIMIT",
    "REVIEW_HEATMAP_TOP_N",
}


def seed_defaults(apps, schema_editor):
    AppSetting = apps.get_model("catalog", "AppSetting")
    existing_keys = set(AppSetting.objects.values_list("key", flat=True))
    for setting_def in SETTING_DEFS:
        if setting_def.key not in _NEW_KEYS:
            continue
        if setting_def.key in existing_keys:
            continue
        AppSetting.objects.create(
            key=setting_def.key,
            value_type=setting_def.value_type,
            value=setting_def.default,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0006_metrics_cache_settings"),
    ]

    operations = [
        migrations.RunPython(seed_defaults, migrations.RunPython.noop),
    ]
