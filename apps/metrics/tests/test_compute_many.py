import datetime

import pytest

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory
from apps.activity.models import AIStatus, PullRequest
from apps.catalog.factories import ProjectFactory, RepositoryFactory
from apps.metrics.models import ScopeType
from apps.metrics.rollups import rebuild
from apps.metrics.services import compute, compute_many
from apps.metrics.types import Scope

pytestmark = pytest.mark.django_db

UNRESTRICTED = ScopeFilter(unrestricted=True, project_ids=None)
UTC = datetime.UTC

DAY_FROM = datetime.date(2026, 6, 10)
DAY_TO = datetime.date(2026, 6, 16)
KEYS = ["prs_merged", "ai_pr_share", "lead_time_p50", "open_prs"]


def _merged(day: datetime.date, **kwargs) -> PullRequest:
    at = datetime.datetime(day.year, day.month, day.day, 10, tzinfo=UTC)
    return PullRequestFactory(
        state=PullRequest.State.MERGED,
        merged_at=at,
        ready_for_review_at=at - datetime.timedelta(hours=5),
        **kwargs,
    )


def _two_repos_with_prs() -> tuple[RepositoryFactory, RepositoryFactory]:
    repo_a = RepositoryFactory()
    repo_b = RepositoryFactory()
    _merged(DAY_FROM, repository=repo_a, ai_status=AIStatus.AI_EXPLICIT)
    _merged(DAY_FROM, repository=repo_a)
    _merged(DAY_FROM + datetime.timedelta(days=1), repository=repo_b, ai_status=AIStatus.AI_EXPLICIT)
    rebuild(DAY_FROM - datetime.timedelta(days=8), DAY_TO)
    return repo_a, repo_b


def test_compute_many_matches_compute_field_by_field_for_every_scope():
    repo_a, repo_b = _two_repos_with_prs()

    many = compute_many(KEYS, ScopeType.REPO, [repo_a.id, repo_b.id], UNRESTRICTED, DAY_FROM, DAY_TO)

    for repo in (repo_a, repo_b):
        scope = Scope(scope_type=ScopeType.REPO, scope_id=repo.id, access=UNRESTRICTED)
        expected = compute(KEYS, scope, DAY_FROM, DAY_TO)
        for key in KEYS:
            actual_result = many[repo.id][key]
            expected_result = expected[key]
            assert actual_result.value == expected_result.value
            assert actual_result.previous_value == expected_result.previous_value
            assert actual_result.delta == expected_result.delta
            assert actual_result.delta_ratio == expected_result.delta_ratio
            assert actual_result.sample_size == expected_result.sample_size
            assert actual_result.previous_sample_size == expected_result.previous_sample_size
            assert actual_result.below_min_sample == expected_result.below_min_sample
            assert actual_result.series == expected_result.series
            assert actual_result.breakdown == expected_result.breakdown


def test_compute_many_counter_only_request_costs_one_rollup_query(django_assert_num_queries):
    """1 query total regardless of scope count: one widened `DailyRollup` fetch covering every
    requested scope id — never one rollup query per scope (RISKS row 10's acceptance criterion for
    this batching path). `MIN_SAMPLE` costs no query here: `_two_repos_with_prs()`'s `rebuild()`
    call already warmed the process-wide `AppSetting` cache (`apps/catalog/services.py`)."""
    repo_a, repo_b = _two_repos_with_prs()

    with django_assert_num_queries(1):
        compute_many(["prs_merged"], ScopeType.REPO, [repo_a.id, repo_b.id], UNRESTRICTED, DAY_FROM, DAY_TO)


def test_compute_many_empty_scope_ids_returns_empty_dict():
    assert compute_many(KEYS, ScopeType.REPO, [], UNRESTRICTED, DAY_FROM, DAY_TO) == {}


def test_compute_many_restricted_access_matches_the_per_scope_path():
    project_a = ProjectFactory()
    repo_a = RepositoryFactory()
    project_a.repositories.add(repo_a)
    project_b = ProjectFactory()
    repo_b = RepositoryFactory()
    project_b.repositories.add(repo_b)

    _merged(DAY_FROM, repository=repo_a, ai_status=AIStatus.AI_EXPLICIT)
    _merged(DAY_FROM, repository=repo_a)
    _merged(DAY_FROM, repository=repo_b, ai_status=AIStatus.AI_EXPLICIT)
    rebuild(DAY_FROM, DAY_TO)

    restricted = ScopeFilter(unrestricted=False, project_ids=frozenset({project_a.id}))
    many = compute_many(
        ["prs_merged", "ai_pr_share"], ScopeType.REPO, [repo_a.id, repo_b.id], restricted, DAY_FROM, DAY_TO
    )

    for repo_id in (repo_a.id, repo_b.id):
        scope = Scope(scope_type=ScopeType.REPO, scope_id=repo_id, access=restricted)
        expected = compute(["prs_merged", "ai_pr_share"], scope, DAY_FROM, DAY_TO)
        assert many[repo_id]["prs_merged"].value == expected["prs_merged"].value
        assert many[repo_id]["prs_merged"].sample_size == expected["prs_merged"].sample_size
        assert many[repo_id]["ai_pr_share"].value == expected["ai_pr_share"].value
