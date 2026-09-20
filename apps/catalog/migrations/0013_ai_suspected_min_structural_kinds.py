# Phase 12, stage 4 — adds the threshold that decides how many distinct structural signal kinds a
# pull request needs before its AI status becomes "suspected". Create-only: a lead who has tuned
# the stored value keeps it.

from django.db import migrations

from apps.catalog.setting_defs import SETTING_DEFS

_NEW_KEYS = {"AI_SUSPECTED_MIN_STRUCTURAL_KINDS"}


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
        ("catalog", "0012_ai_tooling_path_globs_setting"),
    ]

    operations = [
        migrations.RunPython(seed_defaults, migrations.RunPython.noop),
    ]
