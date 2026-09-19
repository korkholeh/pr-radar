import datetime

import pytest

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory
from apps.activity.models import AIStatus, PullRequest
from apps.catalog.factories import RepositoryFactory
from apps.metrics.models import ScopeType
from apps.metrics.rollups import rebuild
from apps.metrics.services import compute, compute_many
from apps.metrics.types import Scope

pytestmark = pytest.mark.django_db

UNRESTRICTED = ScopeFilter(unrestricted=True, project_ids=None)
UTC = datetime.UTC

DAY_FROM = datetime.date(2026, 6, 10)
DAY_TO = datetime.date(2026, 6, 16)
COUNTER_RATIO_KEYS = ["prs_merged", "ai_pr_share"]
DISTRIBUTION_KEY = "lead_time_p50"


def _merged(day: datetime.date, **kwargs) -> PullRequest:
    at = datetime.datetime(day.year, day.month, day.day, 10, tzinfo=UTC)
    return PullRequestFactory(
        state=PullRequest.State.MERGED,
        merged_at=at,
        ready_for_review_at=at - datetime.timedelta(hours=5),
        **kwargs,
    )


def _repo_with_prs() -> RepositoryFactory:
    repo = RepositoryFactory()
    _merged(DAY_FROM, repository=repo, ai_status=AIStatus.AI_EXPLICIT)
    _merged(DAY_FROM + datetime.timedelta(days=1), repository=repo)
    rebuild(DAY_FROM - datetime.timedelta(days=8), DAY_TO)
    return repo


def test_compute_include_series_false_returns_empty_series_for_counter_ratio_and_distribution():
    repo = _repo_with_prs()
    scope = Scope(scope_type=ScopeType.REPO, scope_id=repo.id, access=UNRESTRICTED)

    result_set = compute(
        COUNTER_RATIO_KEYS + [DISTRIBUTION_KEY], scope, DAY_FROM, DAY_TO, include_series=False
    )

    for key in COUNTER_RATIO_KEYS + [DISTRIBUTION_KEY]:
        assert result_set[key].series == ()


def test_compute_include_series_false_does_not_change_value_delta_or_sample_size():
    repo = _repo_with_prs()
    scope = Scope(scope_type=ScopeType.REPO, scope_id=repo.id, access=UNRESTRICTED)

    with_series = compute(COUNTER_RATIO_KEYS + [DISTRIBUTION_KEY], scope, DAY_FROM, DAY_TO)
    without_series = compute(
        COUNTER_RATIO_KEYS + [DISTRIBUTION_KEY], scope, DAY_FROM, DAY_TO, include_series=False
    )

    for key in COUNTER_RATIO_KEYS + [DISTRIBUTION_KEY]:
        assert without_series[key].value == with_series[key].value
        assert without_series[key].delta == with_series[key].delta
        assert without_series[key].sample_size == with_series[key].sample_size
        assert without_series[key].below_min_sample == with_series[key].below_min_sample


def test_compute_many_include_series_false_returns_empty_series_across_scopes():
    repo_a = _repo_with_prs()
    repo_b = _repo_with_prs()

    many = compute_many(
        COUNTER_RATIO_KEYS + [DISTRIBUTION_KEY],
        ScopeType.REPO,
        [repo_a.id, repo_b.id],
        UNRESTRICTED,
        DAY_FROM,
        DAY_TO,
        include_series=False,
    )

    for repo_id in (repo_a.id, repo_b.id):
        for key in COUNTER_RATIO_KEYS + [DISTRIBUTION_KEY]:
            assert many[repo_id][key].series == ()


def test_compute_include_series_true_by_default_is_unchanged():
    repo = _repo_with_prs()
    scope = Scope(scope_type=ScopeType.REPO, scope_id=repo.id, access=UNRESTRICTED)

    result_set = compute(COUNTER_RATIO_KEYS, scope, DAY_FROM, DAY_TO)

    assert len(result_set["prs_merged"].series) > 0
