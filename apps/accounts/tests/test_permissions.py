import importlib

import pytest
from django.apps import apps as django_apps
from django.contrib.auth.models import Group, Permission

from apps.accounts.selectors import scope_for_user

_migration_0004 = importlib.import_module("apps.accounts.migrations.0004_admin_permissions")


@pytest.mark.django_db
def test_admin_group_has_manage_settings():
    admin_group = Group.objects.get(name="admin")
    assert admin_group.permissions.filter(
        content_type__app_label="catalog", codename="manage_settings"
    ).exists()


@pytest.mark.django_db
def test_lead_group_does_not_have_manage_settings():
    lead_group = Group.objects.get(name="lead")
    assert not lead_group.permissions.filter(
        content_type__app_label="catalog", codename="manage_settings"
    ).exists()


@pytest.mark.django_db
def test_grant_manage_settings_noops_when_permission_is_missing(monkeypatch):
    Group.objects.get_or_create(name="admin")
    Permission.objects.filter(content_type__app_label="catalog", codename="manage_settings").delete()
    # Simulate the permission genuinely being gone (e.g. dropped in a later phase) by
    # preventing create_permissions from re-creating it for this call.
    monkeypatch.setattr(_migration_0004, "create_permissions", lambda *a, **kw: None)

    _migration_0004.grant_manage_settings(django_apps, None)  # must not raise


@pytest.mark.django_db
def test_revoke_manage_settings_noops_when_permission_is_missing():
    Group.objects.get_or_create(name="admin")
    Permission.objects.filter(content_type__app_label="catalog", codename="manage_settings").delete()

    _migration_0004.revoke_manage_settings(django_apps, None)  # must not raise


@pytest.mark.django_db
def test_scope_for_user_stays_unrestricted_even_with_access_rows(django_user_model):
    from apps.accounts.models import UserProjectAccess
    from apps.catalog.models import Project

    user = django_user_model.objects.create_user(username="lead2", password="x")
    project = Project.objects.create(name="Alpha", slug="alpha")
    UserProjectAccess.objects.create(user=user, project=project)

    scope = scope_for_user(user)
    assert scope.unrestricted is True
    assert scope.project_ids is None
