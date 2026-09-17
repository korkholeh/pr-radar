import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import AuditEntry, UserPreference
from apps.accounts.selectors import scope_for_user
from apps.accounts.services import record_audit, set_language, set_theme


@pytest.mark.django_db
def test_scope_for_user_is_unrestricted_for_lead_and_admin(lead_user, admin_user):
    assert scope_for_user(lead_user).unrestricted is True
    assert scope_for_user(admin_user).unrestricted is True


@pytest.mark.django_db
def test_record_audit_stores_actor_action_and_changes(lead_user):
    entry = record_audit(
        lead_user, "theme_changed", lead_user, before={"theme": "system"}, after={"theme": "dark"}
    )
    assert isinstance(entry, AuditEntry)
    assert entry.actor == lead_user
    assert entry.action == "theme_changed"
    assert entry.changes == {"before": {"theme": "system"}, "after": {"theme": "dark"}}


@pytest.mark.django_db
def test_set_theme_rejects_unknown_value(lead_user):
    with pytest.raises(ValidationError):
        set_theme(lead_user, "purple")


@pytest.mark.django_db
def test_set_theme_is_idempotent(lead_user):
    set_theme(lead_user, "dark")
    set_theme(lead_user, "dark")
    assert UserPreference.objects.filter(user=lead_user, theme="dark").count() == 1


@pytest.mark.django_db
def test_set_language_rejects_unknown_value(lead_user):
    with pytest.raises(ValidationError):
        set_language(lead_user, "fr")


@pytest.mark.django_db
def test_set_language_is_idempotent(lead_user):
    set_language(lead_user, "uk")
    set_language(lead_user, "uk")
    assert UserPreference.objects.filter(user=lead_user, language="uk").count() == 1
