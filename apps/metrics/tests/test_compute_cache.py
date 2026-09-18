import datetime

import pytest

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory
from apps.activity.models import PullRequest
from apps.catalog.factories import ProjectFactory, RepositoryFactory
from apps.metrics.models import ScopeType
from apps.metrics.rollups import rebuild
from apps.metrics.services import bump_data_version, compute
from apps.metrics.types import Scope

pytestmark = pytest.mark.django_db

UNRESTRICTED = ScopeFilter(unrestricted=True, project_ids=None)
UTC = datetime.UTC

DAY_FROM = datetime.date(2026, 6, 10)
DAY_TO = datetime.date(2026, 6, 16)


def _scope(access: ScopeFilter = UNRESTRICTED) -> Scope:
    return Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=access)


def _merged(day: datetime.date, **kwargs) -> PullRequest:
    at = datetime.datetime(day.year, day.month, day.day, 10, tzinfo=UTC)
    return PullRequestFactory(state=PullRequest.State.MERGED, merged_at=at, **kwargs)


def _seed() -> None:
    for offset in range(3):
        _merged(DAY_FROM + datetime.timedelta(days=offset))
    rebuild(DAY_FROM - datetime.timedelta(days=8), DAY_TO)


def test_second_identical_compute_hits_the_cache(django_assert_num_queries):
    _seed()
    first = compute(["prs_merged"], _scope(), DAY_FROM, DAY_TO)["prs_merged"]

    with django_assert_num_queries(0):
        second = compute(["prs_merged"], _scope(), DAY_FROM, DAY_TO)["prs_merged"]

    assert second.value == first.value
    assert second.sample_size == first.sample_size
    assert second.series == first.series
    assert second.definition is first.definition


def test_bumped_data_version_misses_the_cache():
    _seed()
    compute(["prs_merged"], _scope(), DAY_FROM, DAY_TO)

    _merged(DAY_FROM + datetime.timedelta(days=3))
    rebuild(DAY_FROM - datetime.timedelta(days=8), DAY_TO)
    bump_data_version()

    refreshed = compute(["prs_merged"], _scope(), DAY_FROM, DAY_TO)["prs_merged"]
    assert refreshed.value == 4.0
    assert refreshed.sample_size == 4


def test_two_scope_filters_do_not_share_an_entry_and_each_sees_only_its_own_project(
    django_assert_num_queries,
):
    project_a = ProjectFactory()
    repo_a = RepositoryFactory()
    project_a.repositories.add(repo_a)
    project_b = ProjectFactory()
    repo_b = RepositoryFactory()
    project_b.repositories.add(repo_b)

    _merged(DAY_FROM, repository=repo_a)
    _merged(DAY_FROM, repository=repo_a)
    _merged(DAY_FROM, repository=repo_b)

    restricted_a = ScopeFilter(unrestricted=False, project_ids=frozenset({project_a.id}))
    restricted_b = ScopeFilter(unrestricted=False, project_ids=frozenset({project_b.id}))

    compute(["prs_merged"], _scope(restricted_a), DAY_FROM, DAY_TO)

    # A different access fingerprint must be a cache miss (real queries run), not a reuse of
    # restricted_a's entry (RISKS row 3: a lead must never be served another scope's cached read).
    with pytest.raises(pytest.fail.Exception):
        with django_assert_num_queries(0):
            compute(["prs_merged"], _scope(restricted_b), DAY_FROM, DAY_TO)

    # Both entries now exist independently, are each served from cache on repeat, and each is
    # narrowed to its own project's PRs, not the unrestricted total (RISKS row 3).
    with django_assert_num_queries(0):
        a_again = compute(["prs_merged"], _scope(restricted_a), DAY_FROM, DAY_TO)["prs_merged"]
    with django_assert_num_queries(0):
        b_again = compute(["prs_merged"], _scope(restricted_b), DAY_FROM, DAY_TO)["prs_merged"]
    assert a_again.value == 2.0
    assert b_again.value == 1.0


def test_real_file_based_cache_round_trips_a_metric_result_set():
    _seed()
    result = compute(["prs_merged", "ai_pr_share"], _scope(), DAY_FROM, DAY_TO)

    cached = compute(["prs_merged", "ai_pr_share"], _scope(), DAY_FROM, DAY_TO)

    assert cached["prs_merged"].value == result["prs_merged"].value
    assert cached["ai_pr_share"].value == result["ai_pr_share"].value
    assert cached["ai_pr_share"].definition.key == "ai_pr_share"
