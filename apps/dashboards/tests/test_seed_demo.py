"""Tests for `manage.py seed_demo` (phase 8, task T1): idempotency, the deliberately shared
repository, the pipeline having actually run (rollups + a successful SyncRun), and a bot author's
PRs being excluded from the metrics population. No live GitHub call is possible here -- the
command imports neither `httpx` nor `respx` and never reaches the network."""

import pytest
from django.core.management import call_command

from apps.accounts.selectors import ScopeFilter
from apps.activity.models import PullRequest
from apps.activity.selectors import pull_requests_for_metrics
from apps.catalog.models import Person, Project, Repository
from apps.github_sync.models import SyncRun
from apps.metrics.models import DailyRollup

pytestmark = pytest.mark.django_db

SHARED_REPO_FULL_NAME = "pr-radar-demo-alpha/atlas"
BOT_DISPLAY_NAME = "demo-bot-ci[bot]"


def test_seed_demo_has_no_ai_detection_or_live_github_calls_in_source() -> None:
    """Sanity guard against the intake constraint (CLAUDE.md: no live GitHub call at any point):
    the command's own source imports neither the HTTP client nor its test double."""
    import inspect

    from apps.dashboards.management.commands import seed_demo

    source = inspect.getsource(seed_demo)
    assert "httpx" not in source
    assert "respx" not in source


def test_running_it_twice_creates_no_duplicates() -> None:
    call_command("seed_demo")
    repository_count = Repository.objects.count()
    pull_request_count = PullRequest.objects.count()
    person_count = Person.objects.count()
    project_count = Project.objects.count()

    call_command("seed_demo")

    assert Repository.objects.count() == repository_count
    assert PullRequest.objects.count() == pull_request_count
    assert Person.objects.count() == person_count
    assert Project.objects.count() == project_count


def test_one_repository_belongs_to_two_projects() -> None:
    call_command("seed_demo")

    shared_repository = Repository.objects.get(full_name=SHARED_REPO_FULL_NAME)

    assert Project.objects.filter(repositories=shared_repository).count() == 2


def test_rollups_and_a_successful_sync_run_exist() -> None:
    call_command("seed_demo")

    assert DailyRollup.objects.exists()
    assert SyncRun.objects.filter(status=SyncRun.Status.SUCCESS).exists()


def test_bot_author_prs_are_excluded_from_the_metrics_population() -> None:
    call_command("seed_demo")

    bot_person = Person.objects.get(display_name=BOT_DISPLAY_NAME, is_bot=True)
    bot_pull_request_ids = set(
        PullRequest.objects.filter(author__person=bot_person).values_list("pk", flat=True)
    )
    assert bot_pull_request_ids, "seed_demo must author at least one PR as the bot person."

    metrics_population_ids = set(pull_requests_for_metrics(ScopeFilter()).values_list("pk", flat=True))
    assert bot_pull_request_ids.isdisjoint(metrics_population_ids)


def test_reset_then_fresh_run_is_deterministic() -> None:
    call_command("seed_demo")
    first_counts = (
        Repository.objects.count(),
        Project.objects.count(),
        Person.objects.count(),
        PullRequest.objects.count(),
    )

    call_command("seed_demo", reset=True)
    second_counts = (
        Repository.objects.count(),
        Project.objects.count(),
        Person.objects.count(),
        PullRequest.objects.count(),
    )

    assert first_counts == second_counts
