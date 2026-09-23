"""`apps/dashboards/person_guidance.py`: the person page's recommendations for the lead, built by
fixed rules from the comparison rows and violation statistics the page already has, and rendered
per item in the reader's language."""

from __future__ import annotations

import pytest
from django.utils import translation

from apps.dashboards.person import (
    AIProfile,
    CohortComparison,
    ComparisonRow,
    ViolationStats,
    ViolationStatsRow,
)
from apps.dashboards.person_guidance import build_ai_guidance, build_guidance, render_recommendation
from apps.dashboards.templatetags.dashboards import recommendation_text

MIN_SAMPLE = 5
HOUR = 3600


def _row(
    metric: str,
    person: float | None,
    project: float | None = None,
    org: float | None = None,
    below_min_sample: bool = False,
) -> ComparisonRow:
    return ComparisonRow(
        metric=metric,
        person_value=person,
        person_sample=10,
        project_value=project,
        org_value=org,
        previous_value=None,
        below_min_sample=below_min_sample,
    )


def _stats(*rows: ViolationStatsRow) -> ViolationStats:
    return ViolationStats(rows=list(rows), totals={}, pull_requests=len(rows))


def _violation(rule: str, severity: str, *, total: int, open: int = 0) -> ViolationStatsRow:
    return ViolationStatsRow(
        rule_code=rule,
        severity=severity,
        total=total,
        open=open,
        acknowledged=0,
        waived=0,
        resolved=total - open,
    )


def _merged(count: float) -> ComparisonRow:
    return _row("prs_merged", count, project=100, org=400)


def test_worse_metric_becomes_an_action_and_better_one_a_strength():
    guidance = build_guidance(
        [
            _merged(12),
            _row("rework_rate", 0.6, project=0.3),
            _row("pr_size_p50", 40, project=200),
            _row("reviews_given", 4, project=50),
        ],
        _stats(),
        MIN_SAMPLE,
    )
    assert [item.code for item in guidance.actions] == ["rework_high"]
    assert [item.params["metric"] for item in guidance.strengths] == ["pr_size_p50"]
    assert guidance.baseline_level == "project"
    assert not guidance.small_sample


def test_small_gaps_neutral_metrics_and_small_samples_are_ignored():
    guidance = build_guidance(
        [
            _merged(12),
            _row("rework_rate", 0.33, project=0.3),  # 3 pp: under the points threshold
            _row("lead_time_p50", 11 * HOUR, project=10 * HOUR),  # +10%: under the ratio threshold
            _row("ai_pr_share", 0.9, project=0.1),  # no better side
            _row("churn_21d", 0.9, project=0.1, below_min_sample=True),
            _row("reviews_given", 3, project=50),
        ],
        _stats(),
        MIN_SAMPLE,
    )
    assert guidance.actions == []
    assert guidance.strengths == []


def test_organization_is_the_baseline_without_a_project():
    guidance = build_guidance(
        [_merged(12), _row("pr_size_p50", 500, org=100), _row("reviews_given", 1)],
        _stats(),
        MIN_SAMPLE,
    )
    assert guidance.baseline_level == "organization"
    assert [(item.code, item.params["baseline"]) for item in guidance.actions] == [("pr_size_high", 100)]


def test_small_sample_keeps_only_policy_items():
    guidance = build_guidance(
        [_merged(2), _row("rework_rate", 0.9, project=0.1), _row("reviews_given", 0)],
        _stats(_violation("NO_TESTS", "medium", total=1, open=1)),
        MIN_SAMPLE,
    )
    assert guidance.small_sample
    assert [item.code for item in guidance.actions] == ["violations_open"]


def test_policy_items_come_before_flow_items():
    guidance = build_guidance(
        [_merged(12), _row("lead_time_p50", 30 * HOUR, project=10 * HOUR), _row("reviews_given", 0)],
        _stats(
            _violation("SECRET_ARTIFACT_COMMITTED", "high", total=2, open=1),
            _violation("TASK_LINK_MISSING", "low", total=4, open=2),
        ),
        MIN_SAMPLE,
    )
    assert [item.code for item in guidance.actions] == [
        "violations_open_high",
        "violations_repeated",
        "violations_open",
        "lead_time_high",
        "no_reviews",
    ]
    assert guidance.actions[0].params == {"count": 1, "rules": ["SECRET_ARTIFACT_COMMITTED"]}
    assert guidance.actions[1].params == {"rule": "TASK_LINK_MISSING", "count": 4}


@pytest.mark.parametrize("language", ["en", "uk"])
def test_every_item_renders_a_full_sentence(language):
    guidance = build_guidance(
        [
            _merged(12),
            _row("followup_fix_rate", 0.4, project=0.1),
            _row("churn_21d", 0.5, project=0.1),
            _row("rework_rate", 0.6, project=0.3),
            _row("lead_time_p50", 30 * HOUR, project=10 * HOUR),
            _row("time_to_first_review_p50", 9 * HOUR, project=3 * HOUR),
            _row("pr_size_p50", 500, project=100),
            _row("reviews_given", 0, project=50),
        ],
        _stats(
            _violation("SECRET_ARTIFACT_COMMITTED", "high", total=3, open=3),
            _violation("NO_TESTS", "medium", total=1, open=1),
        ),
        MIN_SAMPLE,
    )
    with translation.override(language):
        texts = [recommendation_text(item, "octocat") for item in guidance.actions]
    assert len(texts) == 10
    for text in texts:
        assert "%(" not in text
        assert "?" not in text
        assert "octocat" in text
        if language == "uk":
            assert "pull request" not in text


def test_unknown_code_renders_itself_and_a_missing_param_renders_a_question_mark():
    assert render_recommendation("nope", {}) == "nope"
    assert "?" in render_recommendation("strength", {"metric": "Rework rate"})


def _profile(
    *,
    ai_count: int = 0,
    statuses: dict[str, int] | None = None,
    quality: list[CohortComparison] | None = None,
    high_risk: float | None = None,
) -> AIProfile:
    return AIProfile(
        status_counts=statuses or {},
        ai_count=ai_count,
        tools=[],
        cohort_quality=quality or [],
        high_risk_rate=high_risk,
        high_risk_sample=10 if high_risk is not None else 0,
    )


def _cohort(metric: str, ai: float, non_ai: float, sample: int = 10) -> CohortComparison:
    return CohortComparison(
        metric=metric, ai_value=ai, ai_sample=sample, non_ai_value=non_ai, non_ai_sample=sample
    )


def _share(person: float, project: float) -> ComparisonRow:
    return _row("ai_pr_share", person, project=project)


def test_ai_rule_violations_move_to_the_ai_section():
    stats = _stats(
        _violation("DISCLOSURE_MISSING", "medium", total=4, open=1),
        _violation("TASK_LINK_MISSING", "low", total=1, open=1),
    )
    comparison = [_merged(12), _share(0.3, 0.3), _row("reviews_given", 2)]
    general = build_guidance(comparison, stats, MIN_SAMPLE)
    ai = build_ai_guidance(comparison, _profile(ai_count=4), stats, MIN_SAMPLE)
    assert [item.code for item in general.actions] == ["violations_open"]
    assert general.actions[0].params == {"count": 1}
    assert [item.code for item in ai.actions] == ["violations_repeated", "violations_open"]


@pytest.mark.parametrize(
    ("merged", "ai_count", "share", "baseline", "level"),
    [
        (12, 0, 0.0, 0.3, "none"),
        (12, 2, 0.1, 0.3, "below"),
        (12, 4, 0.32, 0.3, "in_line"),
        (12, 8, 0.7, 0.3, "above"),
        (3, 2, 0.7, 0.3, "small_sample"),
    ],
)
def test_adoption_level(merged, ai_count, share, baseline, level):
    guidance = build_ai_guidance(
        [_merged(merged), _share(share, baseline)], _profile(ai_count=ai_count), _stats(), MIN_SAMPLE
    )
    assert guidance.level == level


def test_low_adoption_is_raised_only_when_the_team_uses_ai():
    low_team = build_ai_guidance([_merged(12), _share(0.0, 0.05)], _profile(), _stats(), MIN_SAMPLE)
    busy_team = build_ai_guidance([_merged(12), _share(0.0, 0.4)], _profile(), _stats(), MIN_SAMPLE)
    assert low_team.actions == []
    assert [item.code for item in busy_team.actions] == ["ai_adoption_low"]


def test_ai_cohort_quality_is_compared_with_the_persons_other_prs():
    guidance = build_ai_guidance(
        [_merged(12), _share(0.5, 0.3)],
        _profile(
            ai_count=6,
            statuses={"ai_suspected": 2},
            quality=[
                _cohort("rework_rate", 0.8, 0.3),
                _cohort("test_change_ratio", 0.9, 0.3),
                _cohort("churn_21d", 0.9, 0.1, sample=2),  # too few on either side
            ],
        ),
        _stats(),
        MIN_SAMPLE,
    )
    assert [item.code for item in guidance.actions] == ["ai_quality_worse", "ai_undisclosed"]
    assert guidance.actions[0].params["comparisons"] == [
        {"metric": "rework_rate", "value": 0.8, "baseline": 0.3}
    ]
    assert [item.code for item in guidance.strengths] == ["ai_quality_better"]


def test_champion_needs_no_worse_quality_and_no_open_ai_violation():
    comparison = [_merged(12), _share(0.6, 0.3)]
    profile = _profile(ai_count=8, quality=[_cohort("rework_rate", 0.2, 0.3)])
    clean = build_ai_guidance(comparison, profile, _stats(), MIN_SAMPLE)
    open_violation = build_ai_guidance(
        comparison, profile, _stats(_violation("AI_PR_TOO_LARGE", "medium", total=1, open=1)), MIN_SAMPLE
    )
    assert "ai_champion" in [item.code for item in clean.strengths]
    assert "ai_champion" not in [item.code for item in open_violation.strengths]


@pytest.mark.parametrize("language", ["en", "uk"])
def test_every_ai_item_renders_a_full_sentence(language):
    guidance = build_ai_guidance(
        [_merged(12), _share(0.6, 0.3)],
        _profile(
            ai_count=8,
            statuses={"ai_suspected": 2},
            quality=[_cohort("rework_rate", 0.8, 0.3), _cohort("followup_fix_rate", 0.0, 0.2)],
            high_risk=0.25,
        ),
        _stats(_violation("AI_ONLY_APPROVAL", "high", total=1, open=1)),
        MIN_SAMPLE,
    )
    items = guidance.actions + guidance.strengths
    assert {item.code for item in items} == {
        "violations_open_high",
        "ai_quality_worse",
        "ai_undisclosed",
        "ai_high_risk",
        "ai_quality_better",
    }
    with translation.override(language):
        texts = [recommendation_text(item, "octocat") for item in items]
    for text in texts:
        assert "%(" not in text
        assert "?" not in text
