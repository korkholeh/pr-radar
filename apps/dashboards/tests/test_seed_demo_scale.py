"""T9: `manage.py seed_demo --scale {demo,large}` (plan §4). `large` writes 50 repositories and
20,000 pull requests through a bulk path instead of `pipeline.process_pull_request` (measured
~18 minutes at this volume through the per-PR pipeline, which no gate can carry) -- this test
exercises the real, full-volume path via the shared, refcounted `large_scale_seed` fixture
(root `conftest.py`), so this module and `tests/test_performance.py` pay the ~4-minute seed once
between them instead of twice (round 1 review MINOR)."""

from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command

from apps.activity.models import PullRequest
from apps.catalog.models import Repository
from apps.dashboards.management.commands.seed_demo import LARGE_PR_COUNT, LARGE_REPO_COUNT
from apps.metrics.models import DailyRollup

pytestmark = pytest.mark.django_db


def test_large_scale_produces_the_declared_repository_and_pr_counts(large_scale_seed) -> None:
    assert Repository.objects.filter(full_name__startswith="pr-radar-large-").count() == LARGE_REPO_COUNT
    assert PullRequest.objects.filter(github_id__startswith="LARGE_PR_").count() == LARGE_PR_COUNT


def test_large_scale_prs_have_derived_fields_set(large_scale_seed) -> None:
    sample = PullRequest.objects.filter(github_id__startswith="LARGE_PR_", state="merged").first()
    assert sample is not None
    assert sample.size_bucket
    assert sample.ai_status


def test_large_scale_has_rollups_for_the_whole_window(large_scale_seed) -> None:
    assert DailyRollup.objects.exists()


def test_large_scale_rerun_without_reset_raises(large_scale_seed) -> None:
    with pytest.raises(CommandError):
        call_command("seed_demo", scale="large")


@pytest.mark.django_db
def test_demo_scale_is_unchanged_by_the_scale_argument(large_scale_seed) -> None:
    """`--scale demo` (the default) must remain byte-for-byte the existing behaviour --
    `test_seed_demo.py` is the real coverage for it; this only pins that passing `scale="demo"`
    explicitly does not take the large-scale branch. The shared `large_scale_seed` fixture has
    already committed the large-scale rows outside this test's transaction (by design, so the
    other modules that depend on it share one seed), so the assertion is "unchanged count", not
    "absent"."""
    large_count_before = Repository.objects.filter(full_name__startswith="pr-radar-large-").count()
    call_command("seed_demo", scale="demo", days=10)
    assert Repository.objects.filter(full_name__startswith="pr-radar-large-").count() == large_count_before
    assert Repository.objects.filter(full_name__startswith="pr-radar-demo-").exists()
