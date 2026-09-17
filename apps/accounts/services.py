from typing import Any

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import AuditEntry, UserPreference


def record_audit(
    actor: User | None,
    action: str,
    obj: Any,
    before: dict | None = None,
    after: dict | None = None,
) -> AuditEntry:
    return AuditEntry.objects.create(
        actor=actor,
        action=action,
        object_type=obj.__class__.__name__,
        object_id=str(getattr(obj, "pk", obj)),
        changes={"before": before or {}, "after": after or {}},
    )


def set_theme(user: User, value: str) -> UserPreference:
    if value not in UserPreference.Theme.values:
        raise ValidationError(_("Unknown theme: %(value)s") % {"value": value})
    preference, _created = UserPreference.objects.get_or_create(user=user)
    preference.theme = value
    preference.save(update_fields=["theme"])
    return preference


def set_language(user: User, value: str) -> UserPreference:
    if value not in UserPreference.Language.values:
        raise ValidationError(_("Unknown language: %(value)s") % {"value": value})
    preference, _created = UserPreference.objects.get_or_create(user=user)
    preference.language = value
    preference.save(update_fields=["language"])
    return preference
