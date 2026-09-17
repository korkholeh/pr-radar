import os

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

USER_ADMIN = "e2e-admin"
USER_LEAD = "e2e-lead"


class Command(BaseCommand):
    help = "Idempotently create the e2e personas (admin, lead) used by the Playwright suite."

    def handle(self, *args, **options) -> None:
        User = get_user_model()

        admin_password = os.environ.get("E2E_ADMIN_PASSWORD", "admin-password-change-me")
        lead_password = os.environ.get("E2E_LEAD_PASSWORD", "lead-password-change-me")

        admin_group, _ = Group.objects.get_or_create(name="admin")
        lead_group, _ = Group.objects.get_or_create(name="lead")

        admin_user, _ = User.objects.get_or_create(
            username=USER_ADMIN, defaults={"is_staff": True, "is_superuser": True}
        )
        admin_user.is_staff = True
        admin_user.is_superuser = True
        admin_user.set_password(admin_password)
        admin_user.save()
        admin_user.groups.add(admin_group)

        lead_user, _ = User.objects.get_or_create(username=USER_LEAD, defaults={"is_staff": False})
        lead_user.set_password(lead_password)
        lead_user.save()
        lead_user.groups.add(lead_group)

        self.stdout.write(self.style.SUCCESS(f"e2e personas ready: {USER_ADMIN}, {USER_LEAD}"))
