import datetime

import pytest

from apps.activity.factories import (
    CheckStatusFactory,
    PRFileFactory,
    PullRequestFactory,
    ReviewCommentFactory,
    ReviewFactory,
)
from apps.activity.models import AIStatus, CheckStatus, PullRequest
from apps.catalog.factories import IdentityFactory, PersonFactory, ProjectFactory, RepositoryFactory
from apps.metrics.calculators.base import UNRESTRICTED_ACCESS, DayContext
from apps.metrics.models import Cohort, DailyRollup, ScopeType
from apps.metrics.registry import REGISTRY, CounterCalc, RatioCalc
from apps.metrics.rollups import (
    DENOMINATOR_SUFFIX,
    NUMERATOR_SUFFIX,
    NonAdditiveMetricError,
    _cohorts_for,
    _rollup_row,
    rebuild,
    rebuild_dirty,
    rollup_metric_keys,
)
from apps.metrics.selectors import scoped_pull_requests  # noqa: F401  (import sanity for scope helpers)
from apps.metrics.services import mark_dirty
from apps.metrics.timeframe import day_start
from apps.metrics.types import MetricValue, Scope
from apps.policy.factories import PolicyViolationFactory
from apps.policy.models import PolicyViolation

pytestmark = pytest.mark.django_db

DAY = datetime.date(2026, 6, 15)
OTHER_DAY = datetime.date(2026, 6, 16)


def test_rollup_metric_keys_are_additive_only():
    keys = rollup_metric_keys()
    for metric_def in REGISTRY.values():
        if metric_def.kind in {"distribution", "state"}:
            assert metric_def.key not in keys
            assert metric_def.key + "__num" not in keys
            assert metric_def.key + "__den" not in keys
        elif metric_def.kind == "counter":
            assert metric_def.key in keys
        elif metric_def.kind == "ratio":
            assert metric_def.key + "__num" in keys
            assert metric_def.key + "__den" in keys


def test_write_rollups_rejects_a_non_additive_key():
    bad_row = _rollup_row(DAY, ScopeType.GLOBAL, None, Cohort.ALL, "ai_status_breakdown", MetricValue(1.0, 1))
    with pytest.raises(NonAdditiveMetricError):
        from apps.metrics.rollups import write_rollups

        write_rollups([bad_row])


def test_day_with_no_activity_writes_no_rows():
    result = rebuild(DAY, DAY)
    assert result.days == 1
    assert result.rows == 0
    assert not DailyRollup.objects.filter(date=DAY).exists()


def test_repo_in_two_projects_produces_one_global_row_and_one_row_per_project():
    project_a = ProjectFactory()
    project_b = ProjectFactory()
    pr = PullRequestFactory(
        state=PullRequest.State.MERGED, merged_at=datetime.datetime(2026, 6, 15, 10, tzinfo=datetime.UTC)
    )
    project_a.repositories.add(pr.repository)
    project_b.repositories.add(pr.repository)

    rebuild(DAY, DAY)

    rows = DailyRollup.objects.filter(date=DAY, metric_key="prs_merged", cohort=Cohort.ALL)
    scope_pairs = {(row.scope_type, row.scope_id) for row in rows}
    assert (ScopeType.GLOBAL, None) in scope_pairs
    assert (ScopeType.PROJECT, project_a.id) in scope_pairs
    assert (ScopeType.PROJECT, project_b.id) in scope_pairs
    assert (ScopeType.REPO, pr.repository_id) in scope_pairs
    for row in rows:
        assert row.value == 1.0
        assert row.sample_size == 1


def test_rebuild_is_deterministic_after_a_full_delete():
    person = PersonFactory()
    identity = IdentityFactory(person=person)
    PullRequestFactory(
        state=PullRequest.State.MERGED,
        author=identity,
        merged_at=datetime.datetime(2026, 6, 15, 9, tzinfo=datetime.UTC),
        created_at=datetime.datetime(2026, 6, 15, 8, tzinfo=datetime.UTC),
    )
    PullRequestFactory(
        state=PullRequest.State.CLOSED,
        closed_at=datetime.datetime(2026, 6, 15, 11, tzinfo=datetime.UTC),
        created_at=datetime.datetime(2026, 6, 15, 8, tzinfo=datetime.UTC),
    )

    def _snapshot() -> set[tuple]:
        return {
            (row.date, row.scope_type, row.scope_id, row.cohort, row.metric_key, row.value, row.sample_size)
            for row in DailyRollup.objects.filter(date=DAY)
        }

    first = rebuild(DAY, DAY)
    assert first.rows > 0
    before = _snapshot()

    DailyRollup.objects.filter(date=DAY).delete()
    second = rebuild(DAY, DAY)
    after = _snapshot()

    assert second.rows == first.rows
    assert before == after


def test_rebuild_dirty_consumes_and_deletes_dirty_day_rows():
    pr = PullRequestFactory(
        state=PullRequest.State.MERGED,
        created_at=datetime.datetime(2026, 6, 15, 8, tzinfo=datetime.UTC),
        merged_at=datetime.datetime(2026, 6, 15, 9, tzinfo=datetime.UTC),
    )
    mark_dirty(pr.id)
    assert DailyRollup.objects.filter(date=DAY).count() == 0

    from apps.metrics.models import DirtyDay

    assert DirtyDay.objects.filter(date=DAY).exists()

    result = rebuild_dirty()

    assert result.days == 1
    assert not DirtyDay.objects.exists()
    assert DailyRollup.objects.filter(date=DAY, metric_key="prs_merged").exists()


def test_rebuild_dirty_keeps_a_marker_re_marked_concurrently_with_the_rebuild(monkeypatch):
    """A `mark_dirty()` for the same day landing between `rebuild_dirty()`'s read of that day's
    rows and its delete of the `DirtyDay` marker must not have its marker silently deleted — it
    must survive to be retried by the next `rebuild_dirty()` call."""
    from apps.metrics import rollups as rollups_module
    from apps.metrics.models import DirtyDay

    pr = PullRequestFactory(
        state=PullRequest.State.MERGED,
        created_at=datetime.datetime(2026, 6, 15, 8, tzinfo=datetime.UTC),
        merged_at=datetime.datetime(2026, 6, 15, 9, tzinfo=datetime.UTC),
    )
    mark_dirty(pr.id)

    original_build = rollups_module._build_rows_for_day

    def _build_and_re_mark(date):
        rows = original_build(date)
        mark_dirty(pr.id)  # simulates a concurrent sync marking the same day dirty again
        return rows

    monkeypatch.setattr(rollups_module, "_build_rows_for_day", _build_and_re_mark)

    rebuild_dirty()

    assert DirtyDay.objects.filter(date=DAY).exists()


def _read_rollup(scope_type: str, scope_id: int | None, cohort: str, metric_key: str) -> MetricValue:
    row = DailyRollup.objects.filter(
        date=DAY, scope_type=scope_type, scope_id=scope_id, cohort=cohort, metric_key=metric_key
    ).first()
    if row is None:
        return MetricValue.empty()
    return MetricValue(row.value, row.sample_size)


def test_rebuild_matches_the_per_scope_calculator_for_every_additive_metric():
    """`rebuild()` computes every counter/ratio metric via a batched, grouped-aggregate query
    instead of calling the metric once per scope (RISKS row 10 / the query-count fix). This test
    is the oracle that guards that rewrite: for a rich, multi-repo/multi-project/multi-person
    dataset, every rollup row `rebuild()` writes must equal what the metric's own (already unit
    tested) per-scope `daily()`/`numerator()`/`denominator()` function returns when called
    directly — regardless of how the batched code computes it internally."""
    project_a = ProjectFactory()
    project_b = ProjectFactory()
    repo_a = RepositoryFactory()
    repo_b = RepositoryFactory()
    project_a.repositories.add(repo_a)
    project_b.repositories.add(repo_a, repo_b)

    person_1 = PersonFactory()
    person_2 = PersonFactory()
    person_3 = PersonFactory()
    identity_1 = IdentityFactory(person=person_1)
    identity_2 = IdentityFactory(person=person_2)
    identity_3 = IdentityFactory(person=person_3)

    at = datetime.datetime(2026, 6, 15, 10, tzinfo=datetime.UTC)

    pr1 = PullRequestFactory(
        repository=repo_a,
        author=identity_1,
        state=PullRequest.State.MERGED,
        merged_at=at,
        created_at=at,
        ai_status=AIStatus.AI_EXPLICIT,
        review_rounds=2,
        commits_after_first_review=1,
        has_test_changes=True,
        is_rubber_stamp=False,
        is_self_merged=False,
        effective_additions=50,
        effective_deletions=10,
    )
    pr2 = PullRequestFactory(
        repository=repo_a,
        author=identity_2,
        state=PullRequest.State.MERGED,
        merged_at=at,
        created_at=at,
        ai_status=AIStatus.NO_AI,
        review_rounds=None,
        commits_after_first_review=0,
        has_test_changes=False,
        is_rubber_stamp=True,
        is_self_merged=True,
        effective_additions=5,
        effective_deletions=0,
    )
    pr3 = PullRequestFactory(
        repository=repo_b,
        author=identity_1,
        state=PullRequest.State.MERGED,
        merged_at=at,
        created_at=at,
        ai_status=AIStatus.AI_SUSPECTED,
        review_rounds=1,
        commits_after_first_review=2,
        has_test_changes=True,
        effective_additions=20,
        effective_deletions=5,
    )
    PullRequestFactory(
        repository=repo_a,
        author=identity_2,
        state=PullRequest.State.CLOSED,
        closed_at=at,
        created_at=at,
    )
    pr_reverted = PullRequestFactory(
        repository=repo_a, author=identity_1, state=PullRequest.State.MERGED, merged_at=at, created_at=at
    )
    PullRequestFactory(repository=repo_a, author=identity_2, reverts_pr=pr_reverted)

    ReviewFactory(pull_request=pr1, reviewer=identity_3, submitted_at=at)
    ReviewFactory(pull_request=pr2, reviewer=identity_1, submitted_at=at)

    CheckStatusFactory(
        pull_request=pr1, is_first_ci_commit=True, rollup_state=CheckStatus.RollupState.SUCCESS
    )
    CheckStatusFactory(
        pull_request=pr2, is_first_ci_commit=True, rollup_state=CheckStatus.RollupState.FAILURE
    )

    PRFileFactory(pull_request=pr1, is_test=True, is_excluded=False, additions=5, deletions=1)
    ReviewCommentFactory(pull_request=pr1, author=identity_3)
    ReviewCommentFactory(pull_request=pr1, author=identity_3)

    PolicyViolationFactory(
        pull_request=pr1, rule_code=PolicyViolation.RuleCode.NO_TESTS, status=PolicyViolation.Status.OPEN
    )
    disclosure_violation = PolicyViolationFactory(
        pull_request=pr3,
        rule_code=PolicyViolation.RuleCode.DISCLOSURE_MISMATCH,
        status=PolicyViolation.Status.OPEN,
    )
    for violation in PolicyViolation.objects.filter(
        pull_request__in=[pr1, disclosure_violation.pull_request]
    ):
        violation.created_at = day_start(DAY)
        violation.save(update_fields=["created_at"])

    rebuild(DAY, DAY)

    scopes: list[Scope] = [Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=UNRESTRICTED_ACCESS)]
    scopes += [
        Scope(scope_type=ScopeType.PROJECT, scope_id=pid, access=UNRESTRICTED_ACCESS)
        for pid in (project_a.id, project_b.id)
    ]
    scopes += [
        Scope(scope_type=ScopeType.REPO, scope_id=rid, access=UNRESTRICTED_ACCESS)
        for rid in (repo_a.id, repo_b.id)
    ]
    scopes += [
        Scope(scope_type=ScopeType.PERSON, scope_id=pid, access=UNRESTRICTED_ACCESS)
        for pid in (person_1.id, person_2.id, person_3.id)
    ]

    for metric_def in REGISTRY.values():
        calculator = metric_def.calculator
        if not isinstance(calculator, (CounterCalc, RatioCalc)):
            continue
        for cohort in _cohorts_for(metric_def):
            for scope in scopes:
                if scope.scope_type not in metric_def.levels:
                    continue
                ctx = DayContext(scope=scope, cohort=cohort, date=DAY)
                if isinstance(calculator, CounterCalc):
                    expected = calculator.daily(ctx)
                    actual = _read_rollup(scope.scope_type, scope.scope_id, cohort, metric_def.key)
                    assert actual == expected, (metric_def.key, cohort, scope)
                else:
                    expected_num = calculator.numerator(ctx)
                    actual_num = _read_rollup(
                        scope.scope_type, scope.scope_id, cohort, metric_def.key + NUMERATOR_SUFFIX
                    )
                    assert actual_num == expected_num, (metric_def.key, "num", cohort, scope)
                    expected_den = calculator.denominator(ctx)
                    actual_den = _read_rollup(
                        scope.scope_type, scope.scope_id, cohort, metric_def.key + DENOMINATOR_SUFFIX
                    )
                    assert actual_den == expected_den, (metric_def.key, "den", cohort, scope)


def test_rebuild_query_count_is_bounded_regardless_of_scope_count(django_assert_num_queries):
    """Regression guard for RISKS row 10: on 3 projects / 10 repositories / 20 merged PRs for one
    day, the pre-batching implementation issued ~4,500 queries (one per metric x scope x cohort
    combination). The batched implementation issues a fixed number of grouped-aggregate queries
    per metric x cohort, independent of how many scopes are touched — bounded here well under the
    old per-scope cost so a future regression back to that pattern fails the build."""
    projects = [ProjectFactory() for _ in range(3)]
    repos = [RepositoryFactory() for _ in range(10)]
    for index, repo in enumerate(repos):
        projects[index % 3].repositories.add(repo)
    at = datetime.datetime(2026, 6, 15, 10, tzinfo=datetime.UTC)
    for index in range(20):
        PullRequestFactory(
            repository=repos[index % 10],
            state=PullRequest.State.MERGED,
            merged_at=at,
            created_at=at,
        )

    with django_assert_num_queries(600, exact=False):
        rebuild(DAY, DAY)
