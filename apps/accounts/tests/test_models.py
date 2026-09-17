import pytest
from django.contrib.auth.models import Group

from apps.accounts.models import UserPreference


@pytest.mark.django_db
def test_admin_and_lead_groups_exist_after_migrate():
    assert Group.objects.filter(name="admin").exists()
    assert Group.objects.filter(name="lead").exists()


@pytest.mark.django_db
def test_user_preference_defaults(django_user_model):
    user = django_user_model.objects.create_user(username="alice", password="x")
    preference = UserPreference.objects.create(user=user)
    assert preference.theme == UserPreference.Theme.SYSTEM
    assert preference.language == UserPreference.Language.EN


@pytest.mark.django_db
def test_admin_changelist_200_for_superuser(client, admin_user):
    client.force_login(admin_user)
    assert client.get("/admin/accounts/userpreference/").status_code == 200
    assert client.get("/admin/accounts/auditentry/").status_code == 200
