"""T5: every surface that renders a `MetricResult` carries the below-`MIN_SAMPLE` marker, and a
sample at or above the threshold does not (plan §2, acceptance criterion #2). Four of the six
declared surfaces already have dedicated below/above coverage in their own test modules — this
file does not re-derive them, only points at them — and adds the two that had none:

- KPI card: `apps/dashboards/tests/test_kpi_rendering.py::test_below_min_sample_shows_badge_and_grey_class`
- Person comparison row: `apps/dashboards/tests/test_person_comparison.py`
  (`test_build_comparison_hand_computed_values_for_two_person_project`)
- Policy console KPI: `apps/policy/tests/test_views_console.py::test_low_sample_kpi_is_marked`
- Metric table cell: `apps/dashboards/tests/test_small_sample_markers.py` (added this phase, T4)
- XLSX report Summary sheet: `test_xlsx_summary_marks_below_and_above_min_sample` below (new)
- Reviews reviewer-load row: deliberately absent, see `test_reviewer_load_rows_carry_no_metric_result` below

The second half of this file is the regression guard: it greps the tree for the marker strings and
asserts the result equals a declared file set. This catches a *declared* surface losing its marker,
or a new surface gaining one of the marker strings without being added to the declared set — it
cannot catch a wholly new `MetricResult`-rendering surface added with no marker at all, since such a
surface contains none of `_MARKER_PATTERNS` and never enters the grep results (round 1 review
MINOR). Catching that case would need enumerating every template that renders a metric value by some
other signal (e.g. `metric_value`/`extract_value` call sites) and is left as a follow-up; until then,
a new surface still needs a human to remember T4/T5's rule."""

from __future__ import annotations

import datetime
import subprocess
from pathlib import Path

import pytest

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory, ReviewFactory
from apps.catalog.factories import IdentityFactory, PersonFactory, RepositoryFactory
from apps.dashboards.exports.reports import _summary_rows
from apps.dashboards.reviews import reviewer_load
from apps.metrics.models import ScopeType
from apps.metrics.rollups import rebuild
from apps.metrics.services import bump_data_version
from apps.metrics.types import Scope

REPO_ROOT = Path(__file__).resolve().parent.parent

DATE_FROM = datetime.date(2026, 8, 1)
DATE_TO = datetime.date(2026, 8, 31)


def _params():
    from apps.dashboards.params import DashboardParams

    return DashboardParams(
        mode="period",
        preset="custom",
        date_from=DATE_FROM,
        date_to=DATE_TO,
        day=DATE_TO,
        granularity="week",
        granularity_is_auto=False,
        cohort="all",
        project_ids=(),
        repository_ids=(),
        q="",
        sort="",
        page=1,
        table="",
    )


def _repository_with_merged_prs(count: int) -> RepositoryFactory:
    repository = RepositoryFactory()
    for _ in range(count):
        identity = IdentityFactory(person=PersonFactory())
        PullRequestFactory(
            repository=repository,
            author=identity,
            state="merged",
            created_at=datetime.datetime(2026, 8, 5, tzinfo=datetime.UTC),
            merged_at=datetime.datetime(2026, 8, 10, tzinfo=datetime.UTC),
            last_activity_at=datetime.datetime(2026, 8, 10, tzinfo=datetime.UTC),
        )
    return repository


@pytest.mark.django_db
def test_xlsx_summary_marks_below_and_above_min_sample():
    _repository_with_merged_prs(2)
    rebuild(DATE_FROM, DATE_TO)
    bump_data_version()

    scope = Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=ScopeFilter(unrestricted=True))
    rows = _summary_rows(scope, _params(), ["prs_merged"])

    row = rows[0]
    assert row["sample_size"] == 2
    assert row["below_min_sample"] == "Yes"


@pytest.mark.django_db
def test_xlsx_summary_does_not_mark_at_or_above_min_sample():
    _repository_with_merged_prs(6)
    rebuild(DATE_FROM, DATE_TO)
    bump_data_version()

    scope = Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=ScopeFilter(unrestricted=True))
    rows = _summary_rows(scope, _params(), ["prs_merged"])

    row = rows[0]
    assert row["sample_size"] == 6
    assert row["below_min_sample"] == "No"


@pytest.mark.django_db
def test_reviewer_load_rows_carry_no_metric_result():
    """The Reviews page's own table (`reviews.reviewer_load()`) is a plain `Count` per reviewer,
    never a `MetricResult` — there is no `below_min_sample` to carry, and inventing one for a
    count would be a fake sample size (T4's session-1 open question, resolved here: it stays
    excluded from the declared surface list below). Seeds a real reviewer with reviews (round 1
    review MINOR: the previous version asserted nothing, since an empty result satisfied
    `result == []` regardless of what `reviewer_load` actually returned)."""
    author_identity = IdentityFactory(person=PersonFactory())
    reviewer_identity = IdentityFactory(person=PersonFactory())
    pull_request = PullRequestFactory(
        author=author_identity,
        state="merged",
        created_at=datetime.datetime(2026, 8, 5, tzinfo=datetime.UTC),
        merged_at=datetime.datetime(2026, 8, 10, tzinfo=datetime.UTC),
        last_activity_at=datetime.datetime(2026, 8, 10, tzinfo=datetime.UTC),
    )
    ReviewFactory(
        pull_request=pull_request,
        reviewer=reviewer_identity,
        submitted_at=datetime.datetime(2026, 8, 6, tzinfo=datetime.UTC),
    )

    scope = Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=ScopeFilter(unrestricted=True))
    result = reviewer_load(scope, _params())

    assert result != []
    assert all(isinstance(count, int) for _person, count in result)
    assert (reviewer_identity.person, 1) in result


# -- Declared-surface regression guard ------------------------------------------------------------

_DECLARED_MARKER_FILES = frozenset(
    {
        "apps/dashboards/templates/dashboards/partials/kpi_card.html",
        "apps/dashboards/templates/dashboards/partials/person_comparison.html",
        "apps/policy/templates/policy/partials/kpis.html",
        "apps/dashboards/tables.py",
        "apps/dashboards/exports/reports.py",
    }
)

_MARKER_PATTERNS = ("below_min_sample", "small_sample_note", "cell-small-sample", "__low", "data-low-sample")


def _files_matching_markers() -> set[str]:
    matched: set[str] = set()
    for pattern in _MARKER_PATTERNS:
        output = subprocess.run(
            ["git", "grep", "-l", pattern, "--", "apps"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        for line in output.splitlines():
            if line.endswith((".html", ".py")):
                matched.add(line)
    # Definition/computation sites are not rendering surfaces: the field's home
    # (`apps/metrics/types.py`, `apps/metrics/services.py`), the tag's own definition
    # (`apps/dashboards/templatetags/dashboards.py`), the row builder that writes `__low`
    # (`apps/dashboards/rows.py`), and every test module.
    non_surfaces = {
        "apps/metrics/types.py",
        "apps/metrics/services.py",
        "apps/dashboards/templatetags/dashboards.py",
        "apps/dashboards/rows.py",
        "apps/dashboards/person.py",
    }
    return {path for path in matched if path not in non_surfaces and "/tests/" not in path}


def test_declared_marker_surfaces_match_the_repository():
    """Catches a declared surface losing its marker, or a new marker-bearing surface not added to
    `_DECLARED_MARKER_FILES` — not a wholly new `MetricResult` surface added with no marker at all
    (see the module docstring; round 1 review MINOR)."""
    assert _files_matching_markers() == _DECLARED_MARKER_FILES
