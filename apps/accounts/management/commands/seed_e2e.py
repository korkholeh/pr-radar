import datetime
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.catalog.models import Organization, Repository
from apps.connections.models import GitHubConnection
from apps.connections.services import set_token
from apps.github_sync.models import SyncRun

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

        self._seed_github_connections_and_sync()

        self.stdout.write(self.style.SUCCESS(f"e2e personas ready: {USER_ADMIN}, {USER_LEAD}"))

    def _seed_github_connections_and_sync(self) -> None:
        """Rows for the Settings -> Connections and Sync pages, with no live GitHub call:
        one healthy connection, one whose token is about to expire (exercises the banner), a
        repository, and one finished SyncRun."""
        ok_connection, created = GitHubConnection.objects.get_or_create(
            name="e2e-ok",
            defaults={
                "kind": GitHubConnection.Kind.FINE_GRAINED_PAT,
                "status": GitHubConnection.Status.OK,
                "token_login": "e2e-octocat",
                "last_checked_at": timezone.now(),
            },
        )
        if created:
            set_token(ok_connection, "ghp_e2eoktokennotreal0123456789")

        expiring_connection, created = GitHubConnection.objects.get_or_create(
            name="e2e-expiring",
            defaults={
                "kind": GitHubConnection.Kind.FINE_GRAINED_PAT,
                "status": GitHubConnection.Status.OK,
                "token_login": "e2e-octocat-expiring",
                "expires_at": timezone.now() + datetime.timedelta(days=5),
                "last_checked_at": timezone.now(),
            },
        )
        if created:
            set_token(expiring_connection, "ghp_e2eexpiringtokennotreal012345")

        organization, _ = Organization.objects.get_or_create(
            login="e2e-org",
            defaults={"type": Organization.Type.ORG, "github_id": "e2e-org-node-id"},
        )
        repository, _ = Repository.objects.get_or_create(
            full_name="e2e-org/widget",
            defaults={
                "organization": organization,
                "connection": ok_connection,
                "name": "widget",
                "github_id": "e2e-repo-node-id",
                "default_branch": "main",
                "sync_since": (timezone.now() - datetime.timedelta(days=180)).date(),
                "last_synced_at": timezone.now(),
            },
        )

        if not SyncRun.objects.filter(trigger=SyncRun.Trigger.CLI, status=SyncRun.Status.SUCCESS).exists():
            run = SyncRun.objects.create(
                trigger=SyncRun.Trigger.CLI,
                status=SyncRun.Status.SUCCESS,
                started_at=timezone.now() - datetime.timedelta(minutes=5),
                finished_at=timezone.now(),
                stats={"repositories": 1, "pull_requests": 3, "commits": 5, "errors": 0},
                stats_by_connection={
                    str(ok_connection.pk): {
                        "name": ok_connection.name,
                        "repositories": 1,
                        "skipped": 0,
                        "errors": 0,
                        "status": "ok",
                    }
                },
            )
            run.repositories.add(repository)
