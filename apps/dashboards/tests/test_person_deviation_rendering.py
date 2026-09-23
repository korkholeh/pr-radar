"""The person page's comparison table shows how far the person's value sits from their project's
and the organization's: `deviation_text`/`deviation_class` and `partials/person_comparison.html`."""

from __future__ import annotations

from django.template.loader import render_to_string
from django.utils import translation

from apps.dashboards.person import ComparisonRow, Deviation
from apps.dashboards.templatetags.dashboards import deviation_class, deviation_text
from apps.metrics.registry import get_metric


def test_ratio_deviation_is_in_percentage_points():
    with translation.override("en"):
        assert deviation_text(Deviation(delta=0.129, delta_ratio=0.5), "ratio") == "+12.9 pp"
        assert deviation_text(Deviation(delta=-0.042, delta_ratio=-0.2), "ratio") == "-4.2 pp"


def test_other_deviations_are_relative_to_the_baseline():
    assert deviation_text(Deviation(delta=-58.0, delta_ratio=-0.942), "duration") == "-94.2%"
    assert deviation_text(Deviation(delta=20.0, delta_ratio=0.25), "lines") == "+25.0%"


def test_a_gap_that_rounds_to_zero_carries_no_sign():
    with translation.override("en"):
        assert deviation_text(Deviation(delta=0.0, delta_ratio=None), "ratio") == "0.0 pp"
    assert deviation_text(Deviation(delta=0.0001, delta_ratio=0.0001), "duration") == "0.0%"


def test_no_deviation_or_a_zero_baseline_is_an_em_dash():
    assert deviation_text(None, "ratio") == "—"
    assert deviation_text(Deviation(delta=5.0, delta_ratio=None), "duration") == "—"


def test_deviation_colour_follows_the_metrics_direction():
    faster = Deviation(delta=-30.0, delta_ratio=-0.3)
    assert deviation_class(faster, get_metric("lead_time_p50")) == "text-[var(--good)]"
    assert deviation_class(faster, get_metric("disclosure_rate")) == "text-[var(--bad)]"
    assert deviation_class(faster, get_metric("ai_pr_share")) == "text-[var(--neutral)]"


def test_larger_prs_than_the_team_read_as_worse():
    pr_size = get_metric("pr_size_p50")
    assert deviation_class(Deviation(delta=100.0, delta_ratio=1.0), pr_size) == "text-[var(--bad)]"
    assert deviation_class(Deviation(delta=-50.0, delta_ratio=-0.4), pr_size) == "text-[var(--good)]"


def test_deviation_is_neutral_on_a_small_sample_or_within_the_noise_threshold():
    faster = Deviation(delta=-30.0, delta_ratio=-0.3)
    assert deviation_class(faster, get_metric("lead_time_p50"), True) == "text-[var(--neutral)]"
    tiny = Deviation(delta=-1.0, delta_ratio=-0.01)
    assert deviation_class(tiny, get_metric("lead_time_p50")) == "text-[var(--neutral)]"


def _entry(key: str, **overrides: object) -> dict[str, object]:
    row: dict = {
        "metric": key,
        "person_value": 50.0,
        "person_sample": 10,
        "project_value": 100.0,
        "org_value": 200.0,
        "previous_value": None,
        "below_min_sample": False,
        "project_deviation": Deviation(delta=-50.0, delta_ratio=-0.5),
        "org_deviation": Deviation(delta=-150.0, delta_ratio=-0.75),
    }
    row.update(overrides)
    return {"definition": get_metric(key), "row": ComparisonRow(**row)}


def test_comparison_table_renders_both_deviations_under_the_persons_value():
    with translation.override("en"):
        html = render_to_string(
            "dashboards/partials/person_comparison.html", {"comparison": [_entry("lead_time_p50")]}
        )

    assert 'data-testid="comparison-deviation"' in html
    assert "-50.0% vs project" in html
    assert "-75.0% vs organization" in html
    assert "text-[var(--good)]" in html


def test_comparison_legend_says_the_baselines_are_not_averages_and_names_min_sample():
    with translation.override("en"):
        html = render_to_string(
            "dashboards/partials/person_comparison.html",
            {"comparison": [_entry("lead_time_p50")], "min_sample": 7},
        )

    assert 'data-testid="person-comparison-legend"' in html
    assert "Neither is an average of people." in html
    assert "fewer than 7 data points" in html


def test_comparison_table_renders_no_deviation_for_a_counter():
    html = render_to_string(
        "dashboards/partials/person_comparison.html",
        {"comparison": [_entry("prs_merged", project_deviation=None, org_deviation=None)]},
    )

    assert 'data-testid="comparison-deviation"' not in html
