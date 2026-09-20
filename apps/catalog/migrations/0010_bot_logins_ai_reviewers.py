# Adds the AI agent and reviewer logins that carry no `[bot]` suffix to the stored BOT_LOGINS
# value. The stored list is operator-owned, so this is a union rather than an overwrite: entries a
# lead added stay, and the new ones are appended only when absent. A lead who had deliberately
# removed one of these logins will see it come back once — that is the accepted cost of not
# silently discarding their other edits.

from django.db import migrations

from apps.catalog.services import invalidate_settings_cache
from apps.catalog.setting_defs import SETTING_DEFS

_KEY = "BOT_LOGINS"
_ADDED = ("copilot-pull-request-reviewer", "charliecreates", "charliehelps")


def _default_for(key: str) -> object:
    for setting_def in SETTING_DEFS:
        if setting_def.key == key:
            return setting_def.default
    raise LookupError(key)


def add_logins(apps, schema_editor):
    AppSetting = apps.get_model("catalog", "AppSetting")
    row = AppSetting.objects.filter(key=_KEY).first()
    if row is None:
        AppSetting.objects.create(key=_KEY, value_type="list", value=_default_for(_KEY))
        invalidate_settings_cache()
        return

    stored = list(row.value or [])
    known = {str(value).casefold() for value in stored}
    appended = [login for login in _ADDED if login.casefold() not in known]
    if not appended:
        return
    row.value = stored + appended
    row.save(update_fields=["value"])
    # `CatalogConfig.ready()` connects the invalidation signal to the real `AppSetting` class, and a
    # migration writes through a historical model, so the signal does not fire here. Every earlier
    # settings migration only ever *created* keys, where a stale cache is harmless — the cached dict
    # simply lacks the key and `get_setting` falls back to the code default, which is the same value.
    # This one mutates an existing key, so without an explicit delete the old list stays live until
    # something else happens to write a setting.
    invalidate_settings_cache()


def remove_logins(apps, schema_editor):
    AppSetting = apps.get_model("catalog", "AppSetting")
    row = AppSetting.objects.filter(key=_KEY).first()
    if row is None:
        return
    removed = {login.casefold() for login in _ADDED}
    row.value = [value for value in (row.value or []) if str(value).casefold() not in removed]
    row.save(update_fields=["value"])
    invalidate_settings_cache()


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0009_churn_run_settings"),
    ]

    operations = [
        migrations.RunPython(add_logins, remove_logins),
    ]
