import datetime
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.activity.models import PullRequest
from apps.catalog.models import Identity, Organization, Person, Repository
from apps.connections.models import GitHubConnection
from apps.connections.services import set_token
from apps.github_sync.models import SyncRun

USER_ADMIN = "e2e-admin"
USER_LEAD = "e2e-lead"

# apps.catalog.views.IDENTITY_PAGE_SIZE; duplicated here (rather than imported) because seeding
# is data, not behaviour, and the queue-pagination e2e case needs to seed one page and a bit more.
IDENTITY_QUEUE_PAGE_SIZE = 50


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
        self._seed_people_and_identities()

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

    def _seed_people_and_identities(self) -> None:
        """Rows for Settings -> People and the unmapped-identity queue. Each mutating queue
        action (assign/mark-bot/exclude/create-person) gets its own dedicated identity so the
        e2e specs that click those buttons stay independent of run order; the queue-pagination
        rows are never acted on, only listed, so they are stable across the whole suite."""
        repository = Repository.objects.get(full_name="e2e-org/widget")

        mapped_person, _ = Person.objects.get_or_create(display_name="E2E Ada", defaults={"team": "Platform"})
        ada_identity, _ = Identity.objects.get_or_create(
            kind=Identity.Kind.GITHUB_LOGIN, value="e2e-ada", defaults={"person": mapped_person}
        )
        PullRequest.objects.get_or_create(
            repository=repository,
            number=901,
            defaults={
                "github_id": "e2e-pr-ada",
                "author": ada_identity,
                "title": "E2E seeded PR",
                "state": PullRequest.State.MERGED,
                "created_at": timezone.now() - datetime.timedelta(days=1),
                "merged_at": timezone.now(),
            },
        )

        bot_person, _ = Person.objects.get_or_create(display_name="e2e-ci[bot]", defaults={"is_bot": True})
        Identity.objects.get_or_create(
            kind=Identity.Kind.GITHUB_LOGIN, value="e2e-ci[bot]", defaults={"person": bot_person}
        )

        Person.objects.get_or_create(display_name="E2E Editable")
        Person.objects.get_or_create(display_name="E2E Assign Target")

        # The Playwright suite mutates these rows as a side effect of the cases it drives
        # (assign/mark-bot/exclude/create-person/merge), and the orchestrator is allowed to
        # re-run `pytest e2e` against a surface it did not tear down and re-seed in between. So
        # this command must be idempotent in truth, not just in the get_or_create sense: it has
        # to undo whatever the previous run's UI actions did, every time it runs, or a second
        # pass finds an already-consumed queue and fails for reasons that have nothing to do with
        # the product. "E2E UI Created Person" is never seeded, only created by that spec's own
        # form submit, so a stale one from a previous run must be cleared, not merely ignored.
        Person.objects.filter(display_name="E2E UI Created Person").delete()

        for suffix in ["assign", "mark-bot", "exclude", "create-person"]:
            value = f"e2e-{suffix}-target@example.com"
            identity, _ = Identity.objects.get_or_create(kind=Identity.Kind.GIT_EMAIL, value=value)
            if identity.person_id is None:
                continue
            # mark-bot/exclude/create-person attach a fresh Person named after the identity's own
            # value (apps.catalog.identity.create_person_from_identity); "assign" attaches the
            # pre-existing "E2E Assign Target" fixture instead, which must survive the reset.
            auto_created_person = identity.person if identity.person.display_name == value else None
            identity.person = None
            identity.save(update_fields=["person"])
            if auto_created_person is not None:
                auto_created_person.delete()

        merge_source, _ = Person.objects.get_or_create(display_name="E2E Merge Source")
        merge_source_identity, _ = Identity.objects.get_or_create(
            kind=Identity.Kind.GITHUB_LOGIN, value="e2e-merge-source-login"
        )
        if merge_source_identity.person_id != merge_source.pk:
            merge_source_identity.person = merge_source
            merge_source_identity.save(update_fields=["person"])
        Person.objects.get_or_create(display_name="E2E Merge Target")
        Person.objects.get_or_create(display_name="E2E Self Merge Guard")

        # One page and a bit more, so the queue-pagination case exercises a real second page
        # rather than the single-page case every other seeded row lands on. The "zzz-" segment
        # sorts after every dedicated action-target identity above (unmapped_identities() orders
        # by kind, value: "assign"/"create-person"/"exclude"/"mark-bot" all precede "zzz"), so
        # regardless of which of those four other specs have since consumed, these 55 rows are
        # always the *last* 55 in the queue -- always split 50/5 (or more on page 2, never fewer)
        # across exactly two pages, with item 000 always on page 1 and item 054 always on page 2.
        queue_row_count = IDENTITY_QUEUE_PAGE_SIZE + 5
        for i in range(queue_row_count):
            Identity.objects.get_or_create(
                kind=Identity.Kind.GIT_EMAIL, value=f"e2e-zzz-queue-item-{i:03d}@example.com"
            )
