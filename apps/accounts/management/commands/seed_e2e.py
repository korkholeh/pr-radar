import datetime
import json
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.activity.models import AIDisclosure, PullRequest
from apps.ai_detection.models import Confidence, DetectionRule, Detector, Tool
from apps.ai_detection.services import detect_pull_request
from apps.catalog.models import Identity, Organization, Person, Project, Repository
from apps.catalog.services import get_int
from apps.churn.models import ChurnResult
from apps.connections.models import GitHubConnection
from apps.connections.services import set_token
from apps.github_sync.models import SyncRun
from apps.policy.models import AIPolicy, PolicyViolation, SensitivePathRule
from apps.policy.services import evaluate_pull_request

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
        seed_ids = self._seed_ai_detection()
        self._seed_policy()
        seed_ids.update(self._seed_churn())

        self.stdout.write(self.style.SUCCESS(f"e2e personas ready: {USER_ADMIN}, {USER_LEAD}"))
        # Machine-readable line for e2e/conftest.py's `seed_ids` fixture: this phase's PR detail
        # page has no list view yet to click through to it from (phase 9), so a spec that needs
        # its URL reads the pk from here instead of querying the database behind the app's back.
        self.stdout.write(f"E2E_SEED_IDS={json.dumps(seed_ids)}")

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

    def _seed_ai_detection(self) -> dict[str, int]:
        """Rows for Settings -> Detection rules and the PR detail AI section (phase 5's own
        deferral: 'seed_e2e gains detection rows in the e2e step, not here'). The two PRs below
        are run through the real `detect_pull_request()` -- the same function the sync pipeline
        calls -- so the PR page shows a genuinely computed status/evidence/disclosure, not values
        hand-picked to match what the template expects."""
        repository = Repository.objects.get(full_name="e2e-org/widget")

        # "E2E UI Created Rule" is never seeded, only created by that spec's own form submit, so a
        # stale one from a previous unrestarted-surface run must be cleared first (see the People
        # seeding's identical note above about "E2E UI Created Person").
        DetectionRule.objects.filter(name="E2E UI created rule").delete()

        DetectionRule.objects.get_or_create(
            name="E2E footer rule",
            defaults={
                "detector": Detector.PR_BODY_FOOTER,
                "pattern": "Generated with E2E Bot",
                "tool": Tool.CLAUDE_CODE,
                "confidence": Confidence.HIGH,
                "notes": "e2e seed rule -- matches the seeded 'E2E AI-assisted PR' body footer",
            },
        )
        DetectionRule.objects.get_or_create(
            name="E2E editable rule",
            defaults={
                "detector": Detector.LABEL,
                "pattern": "^e2e-editable$",
                "tool": Tool.CURSOR,
                "confidence": Confidence.MEDIUM,
                "notes": "e2e seed rule for the rule-edit case",
            },
        )
        toggle_rule, _ = DetectionRule.objects.get_or_create(
            name="E2E toggle rule",
            defaults={
                "detector": Detector.LABEL,
                "pattern": "^e2e-toggle$",
                "tool": Tool.DEVIN,
                "confidence": Confidence.LOW,
                "notes": "e2e seed rule for the activate/deactivate case",
            },
        )
        if not toggle_rule.is_active:
            # Undoes a previous run's own "Deactivate" click so the toggle case always starts
            # from the same "Yes" state, the same way the identity queue resets above.
            toggle_rule.is_active = True
            toggle_rule.save(update_fields=["is_active"])

        signal_pr, _ = PullRequest.objects.get_or_create(
            repository=repository,
            number=910,
            defaults={
                "github_id": "e2e-pr-ai-signal",
                "title": "E2E AI-assisted PR",
                "state": PullRequest.State.OPEN,
                "created_at": timezone.now() - datetime.timedelta(hours=2),
                "body": (
                    "### AI assistance\n"
                    "- [ ] None\n"
                    "- [ ] Partial\n"
                    "- [x] Substantial\n"
                    "### AI tools used\n"
                    "Claude Code\n"
                    "Generated with E2E Bot\n"
                    "Dry-run marker: e2e-dryrun-target\n"
                ),
            },
        )
        no_signal_pr, _ = PullRequest.objects.get_or_create(
            repository=repository,
            number=911,
            defaults={
                "github_id": "e2e-pr-ai-empty",
                "title": "E2E PR without AI signals",
                "state": PullRequest.State.OPEN,
                "created_at": timezone.now() - datetime.timedelta(hours=1),
                "body": "A plain change with no AI assistance section.",
            },
        )

        detect_pull_request(signal_pr.pk)
        detect_pull_request(no_signal_pr.pk)

        return {
            "ai_detection_signal_pr_pk": signal_pr.pk,
            "ai_detection_no_signal_pr_pk": no_signal_pr.pk,
        }

    def _seed_policy(self) -> None:
        """Rows for the Policy console (/policy/) and its two settings pages. One `AIPolicy`
        version, pinned to a fixed point in the past (`SEED_POLICY_EFFECTIVE_FROM`) rather than
        "N days before now", so it is idempotent across reseeds and always governs every PR below
        (all created well after it, none before it). A handful of `PullRequest` rows are run
        through the real `policy.services.evaluate_pull_request()` -- the same function the sync
        pipeline calls -- so the console shows genuinely computed violations, not hand-picked
        rows. `allowed_tools=[claude_code]` plus `require_disclosure=True` and nothing else keeps
        exactly two rule codes reachable (DISCLOSURE_MISSING, TOOL_NOT_ALLOWED, and
        DISCLOSURE_MISMATCH via a real detected signal) so the e2e filter/severity cases have a
        deterministic, unambiguous set of rows to narrow. The three action-target PRs' violations
        are deleted and re-evaluated on every reseed -- the same reset shape as ai_detection's
        toggle rule and the identity queue above -- so a previous run's own Acknowledge/Waive
        click can never leak into the next session."""
        repository = Repository.objects.get(full_name="e2e-org/widget")

        project, _ = Project.objects.get_or_create(
            slug="e2e-widget-project", defaults={"name": "E2E Widget Project"}
        )
        project.repositories.add(repository)

        seed_effective_from = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        AIPolicy.objects.get_or_create(
            effective_from=seed_effective_from,
            defaults={"allowed_tools": [Tool.CLAUDE_CODE], "require_disclosure": True},
        )

        SensitivePathRule.objects.filter(description__startswith="e2e seed rule -- UI created").delete()
        SensitivePathRule.objects.get_or_create(
            project=None,
            glob="e2e-editable/**",
            defaults={
                "ai_mode": SensitivePathRule.AiMode.NEEDS_EXTRA_REVIEW,
                "description": "e2e seed rule for the edit case",
            },
        )
        toggle_rule, _ = SensitivePathRule.objects.get_or_create(
            project=None,
            glob="e2e-toggle/**",
            defaults={
                "ai_mode": SensitivePathRule.AiMode.FORBIDDEN,
                "description": "e2e seed rule for the toggle case",
            },
        )
        if not toggle_rule.is_active:
            # Undoes a previous run's own "Deactivate" click, the same reset the ai_detection
            # toggle rule needs above.
            toggle_rule.is_active = True
            toggle_rule.save(update_fields=["is_active"])

        now = timezone.now()

        def _get_or_create_pr(number: int, title: str, **fields) -> PullRequest:
            defaults = {
                "github_id": f"e2e-pr-policy-{number}",
                "title": title,
                "state": PullRequest.State.OPEN,
                "created_at": now - datetime.timedelta(days=2),
            }
            defaults.update(fields)
            pr, _ = PullRequest.objects.get_or_create(repository=repository, number=number, defaults=defaults)
            return pr

        ack_pr = _get_or_create_pr(940, "E2E Policy Ack Target")
        comment_pr = _get_or_create_pr(941, "E2E Policy Missing Comment Target")
        waive_pr = _get_or_create_pr(
            942, "E2E Policy Waive Target", ai_disclosure=AIDisclosure.NONE, ai_tools=["cursor"]
        )
        bulk_pr_1 = _get_or_create_pr(943, "E2E Policy Bulk Target One")
        bulk_pr_2 = _get_or_create_pr(944, "E2E Policy Bulk Target Two")
        mismatch_pr, _ = PullRequest.objects.get_or_create(
            repository=repository,
            number=945,
            defaults={
                "github_id": "e2e-pr-policy-945",
                "title": "E2E Policy Disclosure Mismatch PR",
                "state": PullRequest.State.OPEN,
                "created_at": now - datetime.timedelta(days=2),
                # An explicit "None" disclosure plus a real footer-detector match (the same
                # "E2E footer rule" ai_detection seeds above) gives a genuine high-confidence
                # signal with disclosure=none -- DISCLOSURE_MISMATCH's real condition. No
                # "### AI tools used" section, so the disclosure parser adds no raw tool string
                # that would also (mis)trigger TOOL_NOT_ALLOWED here.
                "body": (
                    "### AI assistance\n- [x] None\n- [ ] Partial\n- [ ] Substantial\n\n"
                    "Generated with E2E Bot\n"
                ),
            },
        )
        detect_pull_request(mismatch_pr.pk)

        action_target_prs = [ack_pr, comment_pr, waive_pr, bulk_pr_1, bulk_pr_2]
        PolicyViolation.objects.filter(pull_request__in=action_target_prs).delete()

        for pr in [*action_target_prs, mismatch_pr]:
            evaluate_pull_request(pr.pk)

    def _seed_churn(self) -> dict[str, int]:
        """Rows for the PR detail page's Churn section (phase 10). No git runs in the e2e stack
        (the whole suite forbids live subprocess/network work, same as GitHub itself), so these
        `ChurnResult` rows are written directly rather than through `run_churn` -- exactly the
        shape a real nightly `compute_churn` run would leave behind."""
        repository = Repository.objects.get(full_name="e2e-org/widget")
        window_days = get_int("CHURN_WINDOW_DAYS")
        now = timezone.now()

        ok_pr, _ = PullRequest.objects.get_or_create(
            repository=repository,
            number=950,
            defaults={
                "github_id": "e2e-pr-churn-ok",
                "title": "E2E Churn Measured PR",
                "state": PullRequest.State.MERGED,
                "merge_method": PullRequest.MergeMethod.SQUASH,
                "created_at": now - datetime.timedelta(days=30),
                "merged_at": now - datetime.timedelta(days=25),
            },
        )
        ChurnResult.objects.update_or_create(
            pull_request=ok_pr,
            window_days=window_days,
            defaults={
                "status": ChurnResult.Status.OK,
                "lines_at_merge": 10,
                "lines_surviving": 6,
                "churn_ratio": 0.4,
                "snapshot_sha": "e2e0000000000000000000000000000snapsho",
            },
        )

        unsupported_pr, _ = PullRequest.objects.get_or_create(
            repository=repository,
            number=951,
            defaults={
                "github_id": "e2e-pr-churn-rebase",
                "title": "E2E Churn Rebase PR",
                "state": PullRequest.State.MERGED,
                "merge_method": PullRequest.MergeMethod.REBASE,
                "created_at": now - datetime.timedelta(days=30),
                "merged_at": now - datetime.timedelta(days=25),
            },
        )
        ChurnResult.objects.update_or_create(
            pull_request=unsupported_pr,
            window_days=window_days,
            defaults={"status": ChurnResult.Status.UNSUPPORTED_MERGE_METHOD},
        )

        return {
            "churn_ok_pr_pk": ok_pr.pk,
            "churn_unsupported_pr_pk": unsupported_pr.pk,
        }
