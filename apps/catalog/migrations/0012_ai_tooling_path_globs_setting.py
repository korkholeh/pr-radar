# Phase 12, stage 3 — adds the glob list the repository tooling probe matches a repository's root
# tree against. Create-only, like every settings migration before 0010: a lead who has edited the
# stored list keeps their value.

from django.db import migrations

from apps.catalog.setting_defs import SETTING_DEFS

_NEW_KEYS = {"AI_TOOLING_PATH_GLOBS"}


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
        ("catalog", "0011_repository_ai_tooling"),
    ]

    operations = [
        migrations.RunPython(seed_defaults, migrations.RunPython.noop),
    ]
