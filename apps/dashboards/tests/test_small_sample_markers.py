"""T4: `below_min_sample` reaches the projects/repositories/people metric tables (plan §2). A
repository with a merged-PR count below `MIN_SAMPLE` (5) carries `f"{key}__low"` on its row dict
and the table cell renders the small-sample glyph; one at or above the threshold does not. The
extra `__low` key must stay inert for CSV/XLSX (`extract_value()` reads by `column.key` only)."""

from __future__ import annotations

import datetime

import pytest

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory
from apps.catalog.factories import IdentityFactory, PersonFactory, RepositoryFactory
from apps.dashboards import rows
from apps.dashboards.exports.columns import ExportColumn
from apps.dashboards.exports.csv import stream_csv
from apps.dashboards.params import DashboardParams
from apps.dashboards.tables import _TABLE_CLASSES
from apps.metrics.rollups import rebuild
from apps.metrics.services import bump_data_version

DATE_FROM = datetime.date(2026, 8, 1)
DATE_TO = datetime.date(2026, 8, 31)


def _params() -> DashboardParams:
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
def test_repository_row_carries_low_flag_below_min_sample_only():
    low_repository = _repository_with_merged_prs(2)
    high_repository = _repository_with_merged_prs(6)
    rebuild(DATE_FROM, DATE_TO)
    bump_data_version()

    result_rows = rows.repository_rows(ScopeFilter(unrestricted=True), _params())
    by_id = {row["id"]: row for row in result_rows}

    assert by_id[low_repository.pk]["prs_merged"] == 2
    assert by_id[low_repository.pk]["prs_merged__low"] is True
    assert by_id[high_repository.pk]["prs_merged"] == 6
    assert by_id[high_repository.pk]["prs_merged__low"] is False


def _row(prs_merged: float | None, low: bool) -> dict[str, object]:
    return {
        "id": 1,
        "name": "acme/repo",
        "url": "/repositories/1/",
        "prs_merged": prs_merged,
        "prs_merged__delta": 0.0,
        "prs_merged__low": low,
    }


def test_table_cell_renders_marker_when_row_is_low():
    table = _TABLE_CLASSES["repositories"]([_row(2, True)])
    cell = table.rows[0].get_cell("prs_merged")
    assert 'data-testid="cell-small-sample"' in cell
    assert "≈" in cell
    assert "Small sample" in cell


def test_table_cell_has_no_marker_when_row_is_not_low():
    table = _TABLE_CLASSES["repositories"]([_row(6, False)])
    cell = table.rows[0].get_cell("prs_merged")
    assert 'data-testid="cell-small-sample"' not in cell


def test_low_key_is_inert_for_csv_export():
    column = ExportColumn(key="prs_merged", title="Merged PRs", type="int")
    with_marker = stream_csv([column], [_row(2, True)], "test.csv")
    without_marker = stream_csv([column], [_row(2, False)], "test.csv")
    assert b"".join(with_marker.streaming_content) == b"".join(without_marker.streaming_content)
