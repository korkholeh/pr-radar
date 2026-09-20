# Phase 12, stage 6 — adds the per-repository opt-in for diff analysis. Create-only, and the
# default is the empty list: an upgrade analyses nothing until a lead names a repository, because
# reading the contents of every change is a choice an operator makes, not a default.

from django.db import migrations

from apps.catalog.setting_defs import SETTING_DEFS

_NEW_KEYS = {"DIFF_ANALYSIS_REPOSITORIES"}


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
        ("catalog", "0013_ai_suspected_min_structural_kinds"),
    ]

    operations = [
        migrations.RunPython(seed_defaults, migrations.RunPython.noop),
    ]
