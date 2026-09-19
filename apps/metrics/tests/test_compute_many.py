import datetime

import pytest
from django.utils import timezone

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory, ReviewFactory
from apps.activity.models import AIStatus, PullRequest
from apps.catalog.factories import IdentityFactory, PersonFactory, ProjectFactory, RepositoryFactory
from apps.churn.factories import ChurnResultFactory
from apps.dashboards.rows import PEOPLE_METRIC_KEYS
from apps.metrics.models import Cohort, ScopeType
from apps.metrics.rollups import rebuild
from apps.metrics.services import bump_data_version, compute, compute_many
from apps.metrics.types import Scope
from apps.policy.factories import PolicyViolationFactory
from apps.policy.models import PolicyViolation

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


@pytest.fixture
def people_with_mixed_activity():
    """Round 1 review MAJOR: the new batched per-person calculators
    (`_period_by_person`/`_pr_size_p50_period_by_person`/`_reviewer_response_p50_period_by_person`
    in `flow.py`, `_churn_21d_period_by_person` in `quality.py`,
    `_violations_open_at_date_by_person` in `adoption.py`) had no test comparing them against the
    canonical `compute()` path at PERSON scope. Three people: an AI-cohort PR author with a
    settled `ChurnResult` and an open violation, a non-AI PR author with *no* `ChurnResult` row at
    all (the exact gap the review named — `churn_21d` must resolve to a zero-sample `None` for
    them, not error or silently borrow the other person's value), and a reviewer with no PRs of
    their own who reviews both authors' PRs (so `reviewer_response_p50`/`reviews_given`, which
    group by reviewer rather than author, have a non-empty person too)."""
    today = timezone.now().date()
    date_from = today - datetime.timedelta(days=6)
    date_to = today
    at_9am = datetime.datetime.combine(date_from, datetime.time(9), tzinfo=datetime.UTC)
    merged_at = datetime.datetime.combine(date_to, datetime.time(9), tzinfo=datetime.UTC)
    reviewed_at = datetime.datetime.combine(date_to, datetime.time(12), tzinfo=datetime.UTC)

    author_ai = IdentityFactory(person=PersonFactory())
    author_non_ai = IdentityFactory(person=PersonFactory())
    reviewer = IdentityFactory(person=PersonFactory())

    pr_ai = PullRequestFactory(
        author=author_ai,
        state=PullRequest.State.MERGED,
        ai_status=AIStatus.AI_EXPLICIT,
        created_at=at_9am,
        ready_for_review_at=at_9am,
        merged_at=merged_at,
        effective_additions=40,
        effective_deletions=10,
    )
    pr_non_ai = PullRequestFactory(
        author=author_non_ai,
        state=PullRequest.State.MERGED,
        ai_status=AIStatus.NO_AI,
        created_at=at_9am,
        ready_for_review_at=at_9am,
        merged_at=merged_at,
        effective_additions=100,
        effective_deletions=20,
    )

    ChurnResultFactory(pull_request=pr_ai, churn_ratio=0.2)
    # pr_non_ai deliberately gets no ChurnResult row: author_non_ai must read as zero-sample.

    PolicyViolationFactory(pull_request=pr_ai, status=PolicyViolation.Status.OPEN)

    ReviewFactory(pull_request=pr_ai, reviewer=reviewer, submitted_at=reviewed_at)
    ReviewFactory(pull_request=pr_non_ai, reviewer=reviewer, submitted_at=reviewed_at)

    rebuild(date_from - datetime.timedelta(days=14), date_to)
    bump_data_version()

    return {
        "person_ids": [author_ai.person_id, author_non_ai.person_id, reviewer.person_id],
        "date_from": date_from,
        "date_to": date_to,
    }


@pytest.mark.parametrize("cohort", [Cohort.ALL, Cohort.AI, Cohort.NON_AI])
def test_compute_many_matches_compute_for_person_scope_without_series(people_with_mixed_activity, cohort):
    """The PERSON-scope, `include_series=False` branch (`_other_results_by_person()`) must return
    exactly what the canonical single-scope `compute()` path returns, field by field, for every
    metric the People table renders — the existing
    `test_compute_many_matches_compute_field_by_field_for_every_scope` above runs at REPO scope
    with `include_series=True` and never enters this branch (round 1 review MAJOR)."""
    person_ids = people_with_mixed_activity["person_ids"]
    date_from = people_with_mixed_activity["date_from"]
    date_to = people_with_mixed_activity["date_to"]
    keys = list(PEOPLE_METRIC_KEYS)

    many = compute_many(
        keys,
        ScopeType.PERSON,
        person_ids,
        UNRESTRICTED,
        date_from,
        date_to,
        cohort=cohort,
        include_series=False,
    )

    for person_id in person_ids:
        scope = Scope(scope_type=ScopeType.PERSON, scope_id=person_id, access=UNRESTRICTED)
        expected = compute(keys, scope, date_from, date_to, cohort=cohort, include_series=False)
        for key in keys:
            actual_result = many[person_id][key]
            expected_result = expected[key]
            context = (person_id, key, cohort)
            assert actual_result.value == expected_result.value, context
            assert actual_result.previous_value == expected_result.previous_value, context
            assert actual_result.delta == expected_result.delta, context
            assert actual_result.delta_ratio == expected_result.delta_ratio, context
            assert actual_result.sample_size == expected_result.sample_size, context
            assert actual_result.previous_sample_size == expected_result.previous_sample_size, context
            assert actual_result.below_min_sample == expected_result.below_min_sample, context
