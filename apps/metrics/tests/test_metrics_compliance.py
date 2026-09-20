"""The five compliance metrics (phase 12, stage 8) over hand-computed datasets.

Each case is built from the fact the metric reads — an approval, a check rollup, a matched
sensitive path, a structural signal — never from a `PolicyViolation` row, because these metrics
must report an installation's shape whether or not a lead has switched the matching check on.
"""

import datetime

import pytest

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import (
    CheckStatusFactory,
    PRFileFactory,
    PullRequestFactory,
    ReviewFactory,
)
from apps.activity.models import AIStatus, CheckStatus, PullRequest, Review
from apps.ai_detection.factories import AISignalFactory, SignalRuleFactory
from apps.catalog.factories import (
    IdentityFactory,
    PersonFactory,
    ProjectFactory,
    RepositoryFactory,
)
from apps.metrics.calculators.base import DayContext, ratio_value
from apps.metrics.models import Cohort, ScopeType
from apps.metrics.registry import get_metric
from apps.metrics.types import MetricValue, Scope
from apps.policy.factories import AIPolicyFactory, SensitivePathRuleFactory
from apps.policy.models import SensitivePathRule

pytestmark = pytest.mark.django_db

UNRESTRICTED = ScopeFilter(unrestricted=True, project_ids=None)
DAY = datetime.date(2026, 6, 15)
UTC = datetime.UTC
MERGED_AT = datetime.datetime(2026, 6, 15, 12, tzinfo=UTC)


def _scope() -> Scope:
    return Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=UNRESTRICTED)


def _day_ctx(cohort: str = Cohort.ALL) -> DayContext:
    return DayContext(scope=_scope(), cohort=cohort, date=DAY)


def _ratio(metric_key: str, ctx: DayContext | None = None) -> MetricValue:
    ctx = ctx or _day_ctx()
    metric_def = get_metric(metric_key)
    return ratio_value(metric_def.calculator.numerator(ctx), metric_def.calculator.denominator(ctx))


def _batched_global(metric_key: str, cohort: str = Cohort.ALL) -> MetricValue:
    """The same ratio through the batched path `rollups.rebuild()` uses, at GLOBAL scope — the
    two must agree, or a KPI card and its rollup row would report different numbers."""
    metric_def = get_metric(metric_key)
    key = (ScopeType.GLOBAL, None)
    numerator = metric_def.calculator.numerator_batch(cohort, DAY).get(key, MetricValue.empty())
    denominator = metric_def.calculator.denominator_batch(cohort, DAY).get(key, MetricValue.empty())
    return ratio_value(numerator, denominator)


def _merged_pr(**kwargs) -> PullRequest:
    kwargs.setdefault("state", PullRequest.State.MERGED)
    kwargs.setdefault("merged_at", MERGED_AT)
    return PullRequestFactory(**kwargs)


def _reviewer(*, is_bot: bool = False, login: str | None = None):
    person = PersonFactory(is_bot=is_bot)
    return IdentityFactory(person=person, **({"value": login} if login else {}))


def _approval(pull_request: PullRequest, **reviewer_kwargs) -> Review:
    return ReviewFactory(
        pull_request=pull_request,
        reviewer=_reviewer(**reviewer_kwargs),
        state=Review.State.APPROVED,
        submitted_at=MERGED_AT,
    )


# --- ai_only_approval_rate ----------------------------------------------------------------------


def test_ai_only_approval_rate_counts_a_pr_approved_only_by_a_bot():
    bot_approved = _merged_pr()
    _approval(bot_approved, is_bot=True)
    human_approved = _merged_pr()
    _approval(human_approved)

    assert _ratio("ai_only_approval_rate") == MetricValue(0.5, 2)
    assert _batched_global("ai_only_approval_rate") == MetricValue(0.5, 2)


def test_ai_only_approval_rate_ignores_a_pr_a_human_also_approved():
    both = _merged_pr()
    _approval(both, is_bot=True)
    _approval(both)

    assert _ratio("ai_only_approval_rate") == MetricValue(0.0, 1)


def test_ai_only_approval_rate_excludes_prs_nobody_approved_from_both_sides():
    _merged_pr()  # no review at all
    changes_requested = _merged_pr()
    ReviewFactory(
        pull_request=changes_requested,
        reviewer=_reviewer(),
        state=Review.State.CHANGES_REQUESTED,
        submitted_at=MERGED_AT,
    )
    bot_approved = _merged_pr()
    _approval(bot_approved, is_bot=True)

    assert _ratio("ai_only_approval_rate") == MetricValue(1.0, 1)


def test_ai_only_approval_rate_does_not_count_the_authors_own_approval_as_human():
    """`_human_approver_person_ids()` never counts the author, and neither does this: a PR whose
    only human approval is its own author's, plus a bot's, is still bot-only."""
    author_identity = _reviewer()
    pull_request = _merged_pr(author=author_identity)
    ReviewFactory(
        pull_request=pull_request,
        reviewer=author_identity,
        state=Review.State.APPROVED,
        submitted_at=MERGED_AT,
    )
    _approval(pull_request, is_bot=True)

    assert _ratio("ai_only_approval_rate") == MetricValue(1.0, 1)


# --- quality_gate_bypass_rate -------------------------------------------------------------------


def test_quality_gate_bypass_rate_counts_a_pr_merged_over_a_red_rollup():
    red = _merged_pr()
    CheckStatusFactory(
        pull_request=red,
        rollup_state=CheckStatus.RollupState.FAILURE,
        observed_at=MERGED_AT,
    )
    green = _merged_pr()
    CheckStatusFactory(
        pull_request=green,
        rollup_state=CheckStatus.RollupState.SUCCESS,
        observed_at=MERGED_AT,
    )

    assert _ratio("quality_gate_bypass_rate") == MetricValue(0.5, 2)
    assert _batched_global("quality_gate_bypass_rate") == MetricValue(0.5, 2)


def test_quality_gate_bypass_rate_reads_the_last_rollup_not_the_worst():
    fixed = _merged_pr()
    CheckStatusFactory(
        pull_request=fixed,
        rollup_state=CheckStatus.RollupState.FAILURE,
        observed_at=datetime.datetime(2026, 6, 15, 9, tzinfo=UTC),
    )
    CheckStatusFactory(
        pull_request=fixed,
        rollup_state=CheckStatus.RollupState.SUCCESS,
        observed_at=datetime.datetime(2026, 6, 15, 11, tzinfo=UTC),
    )

    assert _ratio("quality_gate_bypass_rate") == MetricValue(0.0, 1)


def test_quality_gate_bypass_rate_excludes_a_pr_with_no_checks_at_all():
    _merged_pr()  # a repository that runs no checks cannot bypass them
    red = _merged_pr()
    CheckStatusFactory(pull_request=red, rollup_state=CheckStatus.RollupState.ERROR, observed_at=MERGED_AT)

    assert _ratio("quality_gate_bypass_rate") == MetricValue(1.0, 1)


# --- ai_review_coverage -------------------------------------------------------------------------


def test_ai_review_coverage_is_empty_until_an_ai_reviewer_is_named():
    reviewed = _merged_pr()
    ReviewFactory(
        pull_request=reviewed,
        reviewer=_reviewer(login="copilot"),
        state=Review.State.COMMENTED,
        submitted_at=MERGED_AT,
    )

    assert _ratio("ai_review_coverage") == MetricValue(None, 0)


def test_ai_review_coverage_counts_a_review_by_a_configured_login_case_insensitively():
    AIPolicyFactory(ai_reviewer_identities=["Copilot"])
    reviewed = _merged_pr()
    ReviewFactory(
        pull_request=reviewed,
        reviewer=_reviewer(login="copilot"),
        state=Review.State.COMMENTED,
        submitted_at=MERGED_AT,
    )
    unreviewed = _merged_pr()
    ReviewFactory(
        pull_request=unreviewed,
        reviewer=_reviewer(login="a-human"),
        state=Review.State.APPROVED,
        submitted_at=MERGED_AT,
    )

    assert _ratio("ai_review_coverage") == MetricValue(0.5, 2)
    assert _batched_global("ai_review_coverage") == MetricValue(0.5, 2)


# --- high_risk_ai_pr_rate -----------------------------------------------------------------------


def _high_risk_rule() -> SensitivePathRule:
    return SensitivePathRuleFactory(
        glob="**/migrations/**",
        ai_mode=SensitivePathRule.AiMode.ADVISORY,
        risk_level=SensitivePathRule.RiskLevel.HIGH,
    )


def test_high_risk_ai_pr_rate_counts_an_ai_pr_touching_a_high_risk_path():
    rule = _high_risk_rule()
    risky = _merged_pr(ai_status=AIStatus.AI_EXPLICIT)
    PRFileFactory(pull_request=risky, path="app/migrations/0001_initial.py", matched_sensitive_rule=rule)
    ordinary = _merged_pr(ai_status=AIStatus.AI_EXPLICIT)
    PRFileFactory(pull_request=ordinary, path="app/views.py")

    assert _ratio("high_risk_ai_pr_rate") == MetricValue(0.5, 2)
    assert _batched_global("high_risk_ai_pr_rate") == MetricValue(0.5, 2)


def test_high_risk_ai_pr_rate_measures_the_ai_cohort_whatever_cohort_is_asked_for():
    rule = _high_risk_rule()
    ai_pr = _merged_pr(ai_status=AIStatus.AI_EXPLICIT)
    PRFileFactory(pull_request=ai_pr, path="app/migrations/0001_initial.py", matched_sensitive_rule=rule)
    hand_written = _merged_pr(ai_status=AIStatus.NO_AI)
    PRFileFactory(pull_request=hand_written, path="app/migrations/0002_more.py", matched_sensitive_rule=rule)

    assert _ratio("high_risk_ai_pr_rate", _day_ctx(Cohort.NON_AI)) == MetricValue(1.0, 1)


def test_high_risk_ai_pr_rate_ignores_an_excluded_file():
    rule = _high_risk_rule()
    pull_request = _merged_pr(ai_status=AIStatus.AI_EXPLICIT)
    PRFileFactory(
        pull_request=pull_request,
        path="app/migrations/0001_initial.py",
        matched_sensitive_rule=rule,
        is_excluded=True,
    )

    assert _ratio("high_risk_ai_pr_rate") == MetricValue(0.0, 1)


# --- structural_signal_rate ---------------------------------------------------------------------


def test_structural_signal_rate_counts_a_structural_signal_only():
    structural = _merged_pr()
    AISignalFactory(
        pull_request=structural,
        rule=None,
        signal_rule=SignalRuleFactory(),
        evidence="",
        evidence_code="commit_burst",
        evidence_params={"commits": 9},
    )
    regex_only = _merged_pr()
    AISignalFactory(pull_request=regex_only)

    assert _ratio("structural_signal_rate") == MetricValue(0.5, 2)
    assert _batched_global("structural_signal_rate") == MetricValue(0.5, 2)


def test_structural_signal_rate_counts_a_pull_request_once_however_many_signals_it_has():
    pull_request = _merged_pr()
    for kind_params in ({"commits": 9}, {"files": 40}):
        AISignalFactory(
            pull_request=pull_request,
            rule=None,
            signal_rule=SignalRuleFactory(),
            evidence="",
            evidence_code="commit_burst",
            evidence_params=kind_params,
            evidence_hash=f"hash-{kind_params}",
        )
    _merged_pr()

    assert _ratio("structural_signal_rate") == MetricValue(0.5, 2)


# --- scope isolation ----------------------------------------------------------------------------


def test_every_compliance_metric_is_narrowed_by_a_restricted_scope():
    """A restricted `ScopeFilter` bypasses `DailyRollup` and reads the calculator directly, so the
    narrowing has to hold in the querysets above and not only in `compute()`."""
    own_repo = RepositoryFactory(full_name="own/repo")
    own_project = ProjectFactory(name="Own project")
    own_project.repositories.add(own_repo)
    other_repo = RepositoryFactory(full_name="other/repo")
    other_project = ProjectFactory(name="Other project")
    other_project.repositories.add(other_repo)

    AIPolicyFactory(ai_reviewer_identities=["copilot"])
    rule = _high_risk_rule()
    for repository in (own_repo, other_repo):
        pull_request = _merged_pr(repository=repository, ai_status=AIStatus.AI_EXPLICIT)
        _approval(pull_request, is_bot=True)
        CheckStatusFactory(
            pull_request=pull_request,
            rollup_state=CheckStatus.RollupState.FAILURE,
            observed_at=MERGED_AT,
        )
        PRFileFactory(
            pull_request=pull_request,
            path="app/migrations/0001_initial.py",
            matched_sensitive_rule=rule,
        )
        ReviewFactory(
            pull_request=pull_request,
            reviewer=_reviewer(login=f"copilot-{repository.pk}"),
            state=Review.State.COMMENTED,
            submitted_at=MERGED_AT,
        )
        AISignalFactory(
            pull_request=pull_request,
            rule=None,
            signal_rule=SignalRuleFactory(),
            evidence="",
            evidence_code="commit_burst",
            evidence_params={"commits": 9},
        )

    restricted = Scope(
        scope_type=ScopeType.GLOBAL,
        scope_id=None,
        access=ScopeFilter(unrestricted=False, project_ids=frozenset({own_project.pk})),
    )
    ctx = DayContext(scope=restricted, cohort=Cohort.ALL, date=DAY)

    for metric_key in (
        "ai_only_approval_rate",
        "quality_gate_bypass_rate",
        "ai_review_coverage",
        "high_risk_ai_pr_rate",
        "structural_signal_rate",
    ):
        metric_def = get_metric(metric_key)
        # One pull request in scope, one out of it: the denominator proves the narrowing happened
        # and is not simply an empty result.
        assert metric_def.calculator.denominator(ctx).sample_size == 1, metric_key
