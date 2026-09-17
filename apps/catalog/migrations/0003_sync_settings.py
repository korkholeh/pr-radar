# Generated for phase 3 — adds the sync/connection tuning keys to AppSetting.

from django.db import migrations

from apps.catalog.setting_defs import SETTING_DEFS

_NEW_KEYS = {
    "SYNC_OVERLAP_MINUTES",
    "SYNC_PR_PAGE_SIZE",
    "SYNC_NESTED_PAGE_SIZE",
    "RATE_LIMIT_MIN_REMAINING",
    "SYNC_MAX_RETRIES",
    "SYNC_RETRY_MAX_SECONDS",
    "SYNC_LOCK_STALE_MINUTES",
    "TOKEN_EXPIRY_WARNING_DAYS",
    "CONNECTION_RECHECK_MIN_MINUTES",
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
        ("catalog", "0002_app_setting_defaults"),
    ]

    operations = [
        migrations.RunPython(seed_defaults, migrations.RunPython.noop),
    ]
