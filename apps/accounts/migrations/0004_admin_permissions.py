from django.apps import apps as global_apps
from django.contrib.auth.management import create_permissions
from django.db import migrations


def grant_manage_settings(apps, schema_editor):
    # Permissions are normally created by the post_migrate signal, which has not fired yet
    # mid-migration, so the catalog app's manage_settings permission must be created explicitly.
    create_permissions(global_apps.get_app_config("catalog"), apps=global_apps, verbosity=0)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    admin_group = Group.objects.get(name="admin")
    permission = Permission.objects.filter(
        content_type__app_label="catalog", codename="manage_settings"
    ).first()
    if permission is None:
        # A later phase renamed or dropped the permission; nothing to grant.
        return
    admin_group.permissions.add(permission)


def revoke_manage_settings(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    admin_group = Group.objects.get(name="admin")
    permission = Permission.objects.filter(
        content_type__app_label="catalog", codename="manage_settings"
    ).first()
    if permission is None:
        return
    admin_group.permissions.remove(permission)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_userprojectaccess"),
        ("catalog", "0002_app_setting_defaults"),
    ]

    operations = [
        migrations.RunPython(grant_manage_settings, revoke_manage_settings),
    ]
