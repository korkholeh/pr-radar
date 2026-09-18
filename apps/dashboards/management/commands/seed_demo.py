"""Deterministic, idempotent demo dataset for manual testing and phase 8's smoke tests (plan §8,
task T1). Builds two organizations, six repositories, three projects (one repository deliberately
shared by two of them, the case `test_scope_aggregation.py` depends on), twelve people (including
one bot and one `exclude_from_metrics`) and ~250 pull requests spread across the `--days` window,
then runs every seeded PR through the exact same post-sync pipeline
`apps/github_sync/pipeline.py::process_pull_request` runs, rebuilds rollups over the whole window
and writes a successful `SyncRun` row for the "data as of" banner.

Plain ORM `get_or_create()`/`create()` calls only, keyed on stable natural keys (`github_id`,
`display_name`, `slug`, ...) so a second run creates no duplicate rows -- no `factory_boy`, which
is a dev-only dependency and this command must run in a production install.

All randomness is drawn from a single `random.Random(SEED)` so the same `--days` value always
produces the same dataset (module-level state is never touched)."""

from __future__ import annotations

import datetime
import hashlib
import random
from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.activity.models import (
    CheckStatus,
    Commit,
    PRFile,
    PullRequest,
    PullRequestCommit,
    Review,
    ReviewComment,
)
from apps.ai_detection.models import Tool
from apps.catalog.models import Identity, Organization, Person, Project, Repository
from apps.connections.models import GitHubConnection
from apps.github_sync.models import SyncRun
from apps.github_sync.pipeline import process_pull_request
from apps.metrics.rollups import rebuild
from apps.metrics.services import bump_data_version
from apps.metrics.timeframe import day_start, today
from apps.policy.models import AIPolicy

SEED = 20260918
DEFAULT_DAYS = 120
PR_COUNT = 250
CONNECTION_NAME = "PR Radar demo connection"

ORG_DEFS: list[dict[str, str]] = [
    {"login": "pr-radar-demo-alpha", "github_id": "DEMO_ORG_ALPHA"},
    {"login": "pr-radar-demo-beta", "github_id": "DEMO_ORG_BETA"},
]

# (index into ORG_DEFS, repository slug)
REPO_DEFS: list[tuple[int, str]] = [
    (0, "atlas"),
    (0, "beacon"),
    (0, "cascade"),
    (1, "delta"),
    (1, "ember"),
    (1, "flux"),
]

# repo_indexes references positions in REPO_DEFS. Repository 0 ("atlas") is deliberately in both
# "Demo Project Nova" and "Demo Project Vega" -- the acceptance criterion T21 depends on: the
# global scope must count it once, each project must count it in full.
PROJECT_DEFS: list[dict[str, Any]] = [
    {"name": "Demo Project Nova", "slug": "demo-project-nova", "repo_indexes": [0, 1, 2]},
    {"name": "Demo Project Orion", "slug": "demo-project-orion", "repo_indexes": [3, 4]},
    {"name": "Demo Project Vega", "slug": "demo-project-vega", "repo_indexes": [5, 0]},
]

REGULAR_DEV_COUNT = 10
BOT_PERSON_INDEX = 10
EXCLUDED_PERSON_INDEX = 11

PEOPLE_DEFS: list[dict[str, Any]] = [
    {"login": "demo-dev-ana", "display_name": "Ana Demchenko"},
    {"login": "demo-dev-bohdan", "display_name": "Bohdan Sydor"},
    {"login": "demo-dev-carla", "display_name": "Carla Ionescu"},
    {"login": "demo-dev-dmytro", "display_name": "Dmytro Panchenko"},
    {"login": "demo-dev-elif", "display_name": "Elif Karaca"},
    {"login": "demo-dev-farid", "display_name": "Farid Aliyev"},
    {"login": "demo-dev-halyna", "display_name": "Halyna Kovalenko"},
    {"login": "demo-dev-hana", "display_name": "Hana Novak"},
    {"login": "demo-dev-ivan", "display_name": "Ivan Rudenko"},
    {"login": "demo-dev-julia", "display_name": "Julia Marchenko"},
    {"login": "demo-bot-ci", "display_name": "demo-bot-ci[bot]", "is_bot": True},
    {"login": "demo-dev-excluded", "display_name": "Excluded Contractor", "exclude_from_metrics": True},
]

TITLE_VERBS = ["Add", "Fix", "Refactor", "Update", "Remove", "Improve", "Tune", "Rework"]
TITLE_NOUNS = [
    "auth flow",
    "billing job",
    "search index",
    "onboarding flow",
    "cache layer",
    "API client",
    "dashboard widget",
    "test harness",
    "migration script",
    "logging pipeline",
    "webhook handler",
    "export writer",
]
FILE_POOL = [
    "app/auth.py",
    "app/billing.py",
    "app/search.py",
    "app/cache.py",
    "app/api_client.py",
    "app/dashboard_widget.py",
    "app/webhook_handler.py",
    "tests/test_auth.py",
    "tests/test_billing.py",
    "docs/README.md",
    "config/settings.py",
    "package-lock.json",
]
LABEL_POOL = ["bug", "enhancement", "chore", "hotfix"]
AI_TOOL_LINES = ["Claude Code", "GitHub Copilot", "Cursor, Claude Code", "Codex", "Claude"]


class Command(BaseCommand):
    help = (
        "Seeds a deterministic demo dataset (organizations, repositories, projects, people and "
        "~250 pull requests) and runs it through the real detection/policy/metrics pipeline, for "
        "manual testing and phase 8's page smoke test. Idempotent: run it again and no row is "
        "duplicated. --reset wipes what this command created first."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete everything this command previously created before reseeding.",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=DEFAULT_DAYS,
            help=f"Number of days of history to spread the seeded PRs across (default {DEFAULT_DAYS}).",
        )

    def handle(self, *args: object, **options: object) -> None:
        days = int(options["days"])  # type: ignore[call-overload]
        reset = bool(options["reset"])

        date_to = today()
        date_from = date_to - datetime.timedelta(days=days - 1)
        policy_effective_from = day_start(date_from) - datetime.timedelta(days=1)

        if reset:
            self._reset(policy_effective_from)

        with transaction.atomic():
            connection = self._seed_connection()
            organizations = self._seed_organizations()
            repositories = self._seed_repositories(organizations, connection)
            self._seed_projects(repositories)
            people, identities = self._seed_people()
            call_command("seed_detection_rules")
            self._seed_policy(policy_effective_from)

        rng = random.Random(SEED)
        pull_request_ids = self._seed_pull_requests(rng, repositories, identities, date_from, date_to)

        for pull_request_id in pull_request_ids:
            process_pull_request(pull_request_id)

        rebuild(date_from, date_to)
        bump_data_version()
        self._seed_sync_run(repositories)

        self.stdout.write(
            self.style.SUCCESS(
                f"seed_demo: {len(organizations)} organizations, {len(repositories)} repositories, "
                f"{len(PROJECT_DEFS)} projects, {len(people)} people, {len(pull_request_ids)} pull "
                f"requests over {date_from.isoformat()}..{date_to.isoformat()}."
            )
        )

    # -- reset -----------------------------------------------------------------------------------

    def _reset(self, policy_effective_from: datetime.datetime) -> None:
        """Wipes exactly what this command creates. Deleting `Organization` cascades to
        `Repository` (CASCADE on `organization`) and from there to every `PullRequest` and its
        dependents (reviews, comments, files, checks, AI signals, violations -- all CASCADE on
        `pull_request`), so that single delete does almost all of the work. `Repository.connection`
        is `on_delete=PROTECT`, but that only blocks deleting a connection while a *surviving*
        repository still points at it -- by the time we delete the connection below, the
        repositories that referenced it are already gone. `Project` rows are not cascade-deleted by
        a `Repository` delete (the M2M through-table row is dropped, the `Project` row is not), so
        they, the seeded `Person`/`Identity` rows, the seeded `AIPolicy` version and the seeded
        `SyncRun` are each deleted explicitly."""
        org_logins = [org["login"] for org in ORG_DEFS]
        project_slugs = [project["slug"] for project in PROJECT_DEFS]
        person_display_names = [person["display_name"] for person in PEOPLE_DEFS]
        identity_values = [person["login"].strip().casefold() for person in PEOPLE_DEFS]

        Organization.objects.filter(login__in=org_logins).delete()
        GitHubConnection.objects.filter(name=CONNECTION_NAME).delete()
        Project.objects.filter(slug__in=project_slugs).delete()
        Identity.objects.filter(kind=Identity.Kind.GITHUB_LOGIN, value__in=identity_values).delete()
        Person.objects.filter(display_name__in=person_display_names).delete()
        AIPolicy.objects.filter(effective_from=policy_effective_from).delete()
        SyncRun.objects.filter(stats__seed_demo=True).delete()

    # -- catalog -----------------------------------------------------------------------------------

    def _seed_connection(self) -> GitHubConnection:
        connection, _created = GitHubConnection.objects.get_or_create(
            name=CONNECTION_NAME,
            defaults={
                "kind": GitHubConnection.Kind.FINE_GRAINED_PAT,
                "owner_login": ORG_DEFS[0]["login"],
                "status": GitHubConnection.Status.OK,
                "is_active": True,
            },
        )
        return connection

    def _seed_organizations(self) -> list[Organization]:
        organizations = []
        for org_def in ORG_DEFS:
            organization, _created = Organization.objects.get_or_create(
                login=org_def["login"],
                defaults={"type": Organization.Type.ORG, "github_id": org_def["github_id"]},
            )
            organizations.append(organization)
        return organizations

    def _seed_repositories(
        self, organizations: list[Organization], connection: GitHubConnection
    ) -> list[Repository]:
        repositories = []
        for org_index, slug in REPO_DEFS:
            organization = organizations[org_index]
            full_name = f"{organization.login}/{slug}"
            repository, _created = Repository.objects.get_or_create(
                full_name=full_name,
                defaults={
                    "organization": organization,
                    "connection": connection,
                    "name": slug,
                    "github_id": f"DEMO_REPO_{slug.upper()}",
                    "default_branch": "main",
                    "is_active": True,
                },
            )
            repositories.append(repository)
        return repositories

    def _seed_projects(self, repositories: list[Repository]) -> None:
        for project_def in PROJECT_DEFS:
            project, _created = Project.objects.get_or_create(
                slug=project_def["slug"], defaults={"name": project_def["name"]}
            )
            project.repositories.set([repositories[index] for index in project_def["repo_indexes"]])

    def _seed_people(self) -> tuple[list[Person], list[Identity]]:
        people = []
        identities = []
        for person_def in PEOPLE_DEFS:
            person, _created = Person.objects.get_or_create(
                display_name=person_def["display_name"],
                defaults={
                    "is_bot": bool(person_def.get("is_bot", False)),
                    "exclude_from_metrics": bool(person_def.get("exclude_from_metrics", False)),
                    "team": "Demo team",
                },
            )
            identity, _created = Identity.objects.get_or_create(
                kind=Identity.Kind.GITHUB_LOGIN,
                value=person_def["login"],
                defaults={"person": person, "github_id": f"DEMO_IDENT_{person_def['login'].upper()}"},
            )
            people.append(person)
            identities.append(identity)
        return people, identities

    def _seed_policy(self, effective_from: datetime.datetime) -> None:
        AIPolicy.objects.get_or_create(
            effective_from=effective_from,
            defaults={
                "allowed_tools": [Tool.CLAUDE_CODE, Tool.COPILOT, Tool.CURSOR],
                "require_disclosure": True,
                "require_human_approval": True,
                "min_human_approvals": 1,
                "require_tests_for_ai_prs": False,
                "ai_pr_max_effective_lines": 800,
            },
        )

    def _seed_sync_run(self, repositories: list[Repository]) -> None:
        sync_run = SyncRun.objects.filter(stats__seed_demo=True).first()
        finished_at = timezone.now()
        if sync_run is None:
            sync_run = SyncRun.objects.create(
                trigger=SyncRun.Trigger.CLI,
                status=SyncRun.Status.SUCCESS,
                started_at=finished_at - datetime.timedelta(minutes=5),
                finished_at=finished_at,
                stats={"seed_demo": True, "repositories": len(repositories), "pull_requests": PR_COUNT},
            )
        else:
            sync_run.status = SyncRun.Status.SUCCESS
            sync_run.finished_at = finished_at
            sync_run.save(update_fields=["status", "finished_at"])
        sync_run.repositories.set(repositories)

    # -- pull requests -----------------------------------------------------------------------------

    def _seed_pull_requests(
        self,
        rng: random.Random,
        repositories: list[Repository],
        identities: list[Identity],
        date_from: datetime.date,
        date_to: datetime.date,
    ) -> list[int]:
        pull_request_ids: list[int] = []
        repo_number_counters: dict[int, int] = dict.fromkeys((repo.pk for repo in repositories), 0)
        window_days = max((date_to - date_from).days, 0) + 1

        for index in range(PR_COUNT):
            repository = rng.choice(repositories)
            repo_number_counters[repository.pk] += 1
            number = repo_number_counters[repository.pk]

            author_identity, author_index = self._pick_author(rng, index, identities)
            created_at = self._random_created_at(rng, date_from, window_days)

            state, merged_at, closed_at = self._pick_lifecycle(rng, created_at)
            merged_by = self._pick_merged_by(rng, state, author_identity, author_index, identities)

            title = f"{rng.choice(TITLE_VERBS)} {rng.choice(TITLE_NOUNS)}"
            head_ref = self._build_head_ref(rng, index)
            labels = self._build_labels(rng)
            body, _ai_tool_line = self._build_body(rng)

            files = self._build_files(rng)
            additions = sum(f["additions"] for f in files)
            deletions = sum(f["deletions"] for f in files)

            pull_request, created = PullRequest.objects.get_or_create(
                github_id=f"DEMO_PR_{index:04d}",
                defaults={
                    "repository": repository,
                    "number": number,
                    "author": author_identity,
                    "title": title,
                    "body": body,
                    "state": state,
                    "is_draft": state == PullRequest.State.OPEN and rng.random() < 0.25,
                    "base_ref": "main",
                    "head_ref": head_ref,
                    "created_at": created_at,
                    "merged_at": merged_at,
                    "closed_at": closed_at,
                    "merged_by": merged_by,
                    "merge_commit_sha": self._deterministic_sha(f"merge-{index}") if merged_at else "",
                    "merge_method": (
                        rng.choice(
                            [
                                PullRequest.MergeMethod.MERGE,
                                PullRequest.MergeMethod.SQUASH,
                                PullRequest.MergeMethod.REBASE,
                            ]
                        )
                        if merged_at
                        else PullRequest.MergeMethod.UNKNOWN
                    ),
                    "additions": additions,
                    "deletions": deletions,
                    "changed_files": len(files),
                    "labels": labels,
                },
            )
            pull_request_ids.append(pull_request.pk)

            if created:
                self._seed_files(pull_request, files)
                commit_shas = self._seed_commits(
                    rng, pull_request, repository, index, author_identity, created_at
                )
                self._seed_reviews(
                    rng, pull_request, identities, author_index, created_at, closed_at, merged_at
                )
                self._seed_review_comments(
                    rng, pull_request, identities, author_index, created_at, closed_at, merged_at
                )
                self._seed_check_statuses(rng, pull_request, commit_shas)

        return pull_request_ids

    def _pick_author(
        self, rng: random.Random, index: int, identities: list[Identity]
    ) -> tuple[Identity, int]:
        """Forces a handful of PRs onto the bot and the excluded person deterministically (rather
        than leaving it to chance) so both populations are always non-empty, satisfying the
        "bot PRs are excluded from pull_requests_for_metrics" acceptance test regardless of the
        random draws elsewhere."""
        if index % 25 == 0:
            author_index = BOT_PERSON_INDEX
        elif index % 25 == 12:
            author_index = EXCLUDED_PERSON_INDEX
        else:
            author_index = rng.randrange(REGULAR_DEV_COUNT)
        return identities[author_index], author_index

    def _random_created_at(
        self, rng: random.Random, date_from: datetime.date, window_days: int
    ) -> datetime.datetime:
        day = date_from + datetime.timedelta(days=rng.randrange(window_days))
        created_at = day_start(day) + datetime.timedelta(
            hours=rng.randint(6, 20), minutes=rng.randint(0, 59), seconds=rng.randint(0, 59)
        )
        # Guards the tail of the window: "today"'s day_start plus up to ~21h can land after the
        # real current instant if the command runs early in the Kyiv day.
        return min(created_at, timezone.now() - datetime.timedelta(minutes=1))

    def _random_between(
        self, rng: random.Random, start: datetime.datetime, end: datetime.datetime
    ) -> datetime.datetime:
        if end <= start:
            return start
        delta_seconds = int((end - start).total_seconds())
        return start + datetime.timedelta(seconds=rng.randint(0, delta_seconds))

    def _pick_lifecycle(
        self, rng: random.Random, created_at: datetime.datetime
    ) -> tuple[str, datetime.datetime | None, datetime.datetime | None]:
        window_end = max(
            min(created_at + datetime.timedelta(days=10), timezone.now()),
            created_at + datetime.timedelta(seconds=1),
        )
        draw = rng.random()
        if draw < 0.65:
            merged_at = self._random_between(rng, created_at, window_end)
            return PullRequest.State.MERGED, merged_at, merged_at
        if draw < 0.85:
            closed_at = self._random_between(rng, created_at, window_end)
            return PullRequest.State.CLOSED, None, closed_at
        return PullRequest.State.OPEN, None, None

    def _pick_merged_by(
        self,
        rng: random.Random,
        state: str,
        author_identity: Identity,
        author_index: int,
        identities: list[Identity],
    ) -> Identity | None:
        if state != PullRequest.State.MERGED:
            return None
        draw = rng.random()
        if draw < 0.2:
            return author_identity
        if draw < 0.9:
            pool = [i for i in range(REGULAR_DEV_COUNT) if i != author_index] or [author_index]
            return identities[rng.choice(pool)]
        return None

    def _build_head_ref(self, rng: random.Random, index: int) -> str:
        draw = rng.random()
        slug = rng.choice(TITLE_NOUNS).replace(" ", "-")
        if draw < 0.08:
            return f"copilot/{slug}-{index}"
        if draw < 0.16:
            return f"codex/{slug}-{index}"
        return f"feature/{slug}-{index}"

    def _build_labels(self, rng: random.Random) -> list[str]:
        labels = rng.sample(LABEL_POOL, k=rng.randint(0, 2))
        if rng.random() < 0.05:
            labels.append("cursor")
        return labels

    def _build_body(self, rng: random.Random) -> tuple[str, str | None]:
        category = rng.choices(
            ["missing", "none", "partial", "substantial", "ambiguous"], weights=[30, 30, 20, 15, 5]
        )[0]
        lines = ["## Summary", "", "Demo PR body generated by seed_demo for manual testing.", ""]
        ai_tool_line: str | None = None
        if category != "missing":
            lines += ["## AI assistance", ""]
            if category == "none":
                lines += ["- [x] None", "- [ ] Partial", "- [ ] Substantial"]
            elif category == "partial":
                lines += ["- [ ] None", "- [x] Partial", "- [ ] Substantial"]
            elif category == "substantial":
                lines += ["- [ ] None", "- [ ] Partial", "- [x] Substantial"]
            else:  # ambiguous
                lines += ["- [ ] None", "- [x] Partial", "- [x] Substantial"]
            if category in ("partial", "substantial"):
                ai_tool_line = rng.choice(AI_TOOL_LINES)
                lines += ["", "## AI tools used", "", ai_tool_line]
        if rng.random() < 0.15:
            lines += ["", "---", "🤖 Generated with [Claude Code](https://claude.ai/code)"]
        return "\n".join(lines), ai_tool_line

    def _build_files(self, rng: random.Random) -> list[dict[str, Any]]:
        paths = rng.sample(FILE_POOL, k=rng.randint(1, min(6, len(FILE_POOL))))
        files = []
        for path in paths:
            files.append(
                {
                    "path": path,
                    "status": rng.choice(["added", "modified", "modified", "removed"]),
                    "additions": rng.randint(1, 150),
                    "deletions": rng.randint(0, 80),
                }
            )
        return files

    def _deterministic_sha(self, seed: str) -> str:
        return hashlib.sha1(f"seed-demo-{seed}".encode()).hexdigest()

    def _seed_files(self, pull_request: PullRequest, files: list[dict[str, Any]]) -> None:
        for file_def in files:
            PRFile.objects.get_or_create(
                pull_request=pull_request,
                path=file_def["path"],
                defaults={
                    "status": file_def["status"],
                    "additions": file_def["additions"],
                    "deletions": file_def["deletions"],
                },
            )

    def _seed_commits(
        self,
        rng: random.Random,
        pull_request: PullRequest,
        repository: Repository,
        index: int,
        author_identity: Identity,
        created_at: datetime.datetime,
    ) -> list[str]:
        commit_count = rng.randint(1, 4)
        shas = []
        for position in range(commit_count):
            sha = self._deterministic_sha(f"pr-{index}-commit-{position}")
            committed_at = created_at - datetime.timedelta(
                hours=rng.randint(0, 48), minutes=rng.randint(0, 59)
            )
            trailers: dict[str, list[str]] = {}
            if position == 0 and rng.random() < 0.1:
                trailers = {"Co-Authored-By": ["Claude <noreply@anthropic.com>"]}
            commit, _created = Commit.objects.get_or_create(
                repository=repository,
                sha=sha,
                defaults={
                    "author_identity": author_identity,
                    "committer_identity": author_identity,
                    "authored_at": committed_at,
                    "committed_at": committed_at,
                    "message": f"Commit {position} for demo PR {index}",
                    "additions": rng.randint(1, 100),
                    "deletions": rng.randint(0, 50),
                    "trailers": trailers,
                },
            )
            PullRequestCommit.objects.get_or_create(
                pull_request=pull_request, commit=commit, defaults={"position": position}
            )
            shas.append(sha)
        return shas

    def _seed_reviews(
        self,
        rng: random.Random,
        pull_request: PullRequest,
        identities: list[Identity],
        author_index: int,
        created_at: datetime.datetime,
        closed_at: datetime.datetime | None,
        merged_at: datetime.datetime | None,
    ) -> None:
        if rng.random() >= 0.7:
            return
        end_bound = closed_at or merged_at or timezone.now()
        pool = [i for i in range(REGULAR_DEV_COUNT) if i != author_index] or list(range(REGULAR_DEV_COUNT))
        for review_index in range(rng.randint(1, 3)):
            reviewer_index = rng.choice(pool)
            state = rng.choices(
                [Review.State.APPROVED, Review.State.CHANGES_REQUESTED, Review.State.COMMENTED],
                weights=[60, 20, 20],
            )[0]
            submitted_at = self._random_between(rng, created_at, end_bound)
            Review.objects.get_or_create(
                github_id=f"DEMO_REVIEW_{pull_request.github_id}_{review_index}",
                defaults={
                    "pull_request": pull_request,
                    "reviewer": identities[reviewer_index],
                    "state": state,
                    "submitted_at": submitted_at,
                    "body_length": rng.randint(0, 400),
                    "comments_count": rng.randint(0, 5),
                },
            )

    def _seed_review_comments(
        self,
        rng: random.Random,
        pull_request: PullRequest,
        identities: list[Identity],
        author_index: int,
        created_at: datetime.datetime,
        closed_at: datetime.datetime | None,
        merged_at: datetime.datetime | None,
    ) -> None:
        if rng.random() >= 0.4:
            return
        end_bound = closed_at or merged_at or timezone.now()
        pool = [i for i in range(REGULAR_DEV_COUNT) if i != author_index] or list(range(REGULAR_DEV_COUNT))
        for comment_index in range(rng.randint(1, 4)):
            commenter_index = rng.choice(pool)
            ReviewComment.objects.get_or_create(
                github_id=f"DEMO_COMMENT_{pull_request.github_id}_{comment_index}",
                defaults={
                    "pull_request": pull_request,
                    "author": identities[commenter_index],
                    "created_at": self._random_between(rng, created_at, end_bound),
                    "is_review_thread": rng.random() < 0.5,
                    "body_length": rng.randint(1, 300),
                },
            )

    def _seed_check_statuses(
        self, rng: random.Random, pull_request: PullRequest, commit_shas: list[str]
    ) -> None:
        if not commit_shas or rng.random() >= 0.8:
            return
        state = rng.choices(
            [
                CheckStatus.RollupState.SUCCESS,
                CheckStatus.RollupState.FAILURE,
                CheckStatus.RollupState.PENDING,
                CheckStatus.RollupState.ERROR,
            ],
            weights=[75, 15, 5, 5],
        )[0]
        CheckStatus.objects.get_or_create(
            pull_request=pull_request,
            commit_sha=commit_shas[-1],
            defaults={
                "is_first_ci_commit": True,
                "rollup_state": state,
                "observed_at": pull_request.created_at + datetime.timedelta(minutes=rng.randint(5, 90)),
            },
        )
