import logging
from typing import Any

from django.core.exceptions import ValidationError

from apps.catalog.models import AppSetting
from apps.catalog.setting_defs import SETTING_DEFS

logger = logging.getLogger(__name__)

_DEFS_BY_KEY = {d.key: d for d in SETTING_DEFS}

_PYTHON_TYPES: dict[str, type | tuple[type, ...]] = {
    "bool": bool,
    "int": int,
    "float": (int, float),
    "str": str,
    "list": list,
    "dict": dict,
}


class UnknownSettingError(KeyError):
    pass


def _validate_type(value_type: str, value: object) -> None:
    expected = _PYTHON_TYPES.get(value_type)
    if expected is None:
        raise ValidationError(f"Unknown value_type {value_type!r}.")
    # bool is a subclass of int in Python; keep the two apart explicitly.
    if value_type != "bool" and isinstance(value, bool):
        raise ValidationError(f"Expected {value_type} for value_type {value_type!r}, got bool.")
    if not isinstance(value, expected):
        raise ValidationError(
            f"Expected {value_type} for value_type {value_type!r}, got {type(value).__name__}."
        )


def get_setting(key: str) -> Any:
    setting_def = _DEFS_BY_KEY.get(key)
    if setting_def is None:
        raise UnknownSettingError(key)
    row = AppSetting.objects.filter(key=key).first()
    if row is None:
        return setting_def.default
    try:
        _validate_type(setting_def.value_type, row.value)
    except ValidationError:
        logger.warning("AppSetting %r has a corrupt value; falling back to the code default.", key)
        return setting_def.default
    return row.value


def get_int(key: str) -> int:
    return int(get_setting(key))


def get_bool(key: str) -> bool:
    return bool(get_setting(key))


def get_str(key: str) -> str:
    return str(get_setting(key))


def get_list(key: str) -> list[Any]:
    value = get_setting(key)
    assert isinstance(value, list)
    return value


def get_dict(key: str) -> dict[str, Any]:
    value = get_setting(key)
    assert isinstance(value, dict)
    return value


def set_setting(key: str, value: object) -> AppSetting:
    setting_def = _DEFS_BY_KEY.get(key)
    if setting_def is None:
        raise UnknownSettingError(key)
    _validate_type(setting_def.value_type, value)
    row, _created = AppSetting.objects.update_or_create(
        key=key,
        defaults={
            "value_type": setting_def.value_type,
            "value": value,
        },
    )
    return row


def seed_app_settings(model: type[AppSetting]) -> int:
    """Idempotent: creates a row for every SETTING_DEFS key that is missing, touches nothing that exists."""
    existing_keys = set(model.objects.values_list("key", flat=True))
    created = 0
    for setting_def in SETTING_DEFS:
        if setting_def.key in existing_keys:
            continue
        model.objects.create(
            key=setting_def.key,
            value_type=setting_def.value_type,
            value=setting_def.default,
        )
        created += 1
    return created
