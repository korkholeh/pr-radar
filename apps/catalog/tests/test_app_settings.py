import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import UserPreference
from apps.catalog.models import AppSetting
from apps.catalog.services import (
    UnknownSettingError,
    get_bool,
    get_dict,
    get_int,
    get_list,
    get_setting,
    get_str,
    seed_app_settings,
    set_setting,
)
from apps.catalog.setting_defs import SETTING_DEFS


@pytest.mark.django_db
@pytest.mark.parametrize("setting_def", SETTING_DEFS, ids=lambda d: d.key)
def test_every_setting_default_is_seeded_by_migration(setting_def):
    row = AppSetting.objects.get(key=setting_def.key)
    assert row.value == setting_def.default
    assert row.value_type == setting_def.value_type


@pytest.mark.django_db
def test_seed_app_settings_is_idempotent():
    created_again = seed_app_settings(AppSetting)
    assert created_again == 0

    row = AppSetting.objects.get(key="BACKFILL_DAYS")
    row.value = 999
    row.save()
    seed_app_settings(AppSetting)
    row.refresh_from_db()
    assert row.value == 999


@pytest.mark.django_db
def test_sync_settings_migration_is_idempotent():
    import importlib

    from django.apps import apps as apps_registry

    migration_module = importlib.import_module("apps.catalog.migrations.0003_sync_settings")
    migration_module.seed_defaults(apps_registry, None)
    row = AppSetting.objects.get(key="SYNC_MAX_RETRIES")
    row.value = 3
    row.save()
    migration_module.seed_defaults(apps_registry, None)
    row.refresh_from_db()
    assert row.value == 3


@pytest.mark.django_db
def test_ai_detection_settings_migration_is_idempotent():
    import importlib

    from django.apps import apps as apps_registry

    migration_module = importlib.import_module("apps.catalog.migrations.0004_ai_detection_settings")
    migration_module.seed_defaults(apps_registry, None)
    row = AppSetting.objects.get(key="DETECTION_DRY_RUN_PR_COUNT")
    row.value = 7
    row.save()
    migration_module.seed_defaults(apps_registry, None)
    row.refresh_from_db()
    assert row.value == 7


@pytest.mark.django_db
def test_policy_settings_migration_is_idempotent():
    import importlib

    from django.apps import apps as apps_registry

    migration_module = importlib.import_module("apps.catalog.migrations.0005_policy_settings")
    migration_module.seed_defaults(apps_registry, None)
    row = AppSetting.objects.get(key="VIOLATIONS_PAGE_SIZE")
    row.value = 25
    row.save()
    migration_module.seed_defaults(apps_registry, None)
    row.refresh_from_db()
    assert row.value == 25


@pytest.mark.django_db
def test_policy_setting_falls_back_to_default_on_corrupt_stored_value():
    row = AppSetting.objects.get(key="POLICY_VIOLATION_PATHS_IN_PARAMS")
    row.value = "not-an-int"
    row.save()
    assert get_setting("POLICY_VIOLATION_PATHS_IN_PARAMS") == 20


@pytest.mark.django_db
def test_get_setting_unknown_key_raises():
    with pytest.raises(UnknownSettingError):
        get_setting("NOT_A_REAL_KEY")


@pytest.mark.django_db
def test_get_setting_missing_row_returns_code_default():
    AppSetting.objects.filter(key="MIN_SAMPLE").delete()
    assert get_setting("MIN_SAMPLE") == 5


@pytest.mark.django_db
def test_get_setting_falls_back_to_default_on_corrupt_stored_value():
    # save() bypasses AppSetting.clean(), so this is the only way a bad (value_type, value)
    # pair reaches the database — get_setting() must not propagate it to a caller.
    row = AppSetting.objects.get(key="BACKFILL_DAYS")
    row.value = "not-an-int"
    row.save()
    assert get_setting("BACKFILL_DAYS") == 180


@pytest.mark.django_db
def test_seeded_row_never_stores_a_rendered_description():
    # System-generated text is code + params, rendered at read time — never a stored English
    # sentence (CLAUDE.md). The registry provides the description; the row does not.
    row = AppSetting.objects.get(key="BACKFILL_DAYS")
    assert row.description == ""


@pytest.mark.django_db
def test_set_setting_wrong_type_raises():
    with pytest.raises(ValidationError):
        set_setting("BACKFILL_DAYS", "not-an-int")


@pytest.mark.django_db
def test_typed_accessors_return_python_types():
    assert isinstance(get_int("BACKFILL_DAYS"), int)
    assert isinstance(get_bool("AI_COHORT_INCLUDE_SUSPECTED"), bool)
    assert isinstance(get_str("DURATION_MODE"), str)
    assert isinstance(get_list("BOT_LOGINS"), list)
    assert isinstance(get_dict("PR_SIZE_BUCKETS"), dict)
    assert isinstance(get_int("DETECTION_DRY_RUN_PR_COUNT"), int)
    assert isinstance(get_dict("DISCLOSURE_TOOL_ALIASES"), dict)
    assert isinstance(get_list("POLICY_DISABLED_RULES"), list)
    assert isinstance(get_int("POLICY_VIOLATION_PATHS_IN_PARAMS"), int)
    assert isinstance(get_int("VIOLATIONS_PAGE_SIZE"), int)


@pytest.mark.django_db
def test_default_theme_and_language_match_user_preference_defaults():
    assert get_str("DEFAULT_THEME") == UserPreference._meta.get_field("theme").default
    assert get_str("DEFAULT_UI_LANGUAGE") == UserPreference._meta.get_field("language").default
