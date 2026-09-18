"""T14: `ExportColumn`, the type vocabulary and `TABLE_SPECS`."""

from __future__ import annotations

import datetime

import pytest

from apps.dashboards.exports.columns import (
    COLUMN_TYPES,
    TABLE_SPECS,
    ExportColumn,
    extract_value,
)
from apps.dashboards.exports.csv import stream_csv
from apps.dashboards.exports.xlsx import write_xlsx
from apps.metrics.registry import get_metric

_SAMPLE_VALUES: dict[str, object] = {
    "text": "hello",
    "int": 3,
    "float": 1.5,
    "percent": 0.42,
    "duration": 3600.0,
    "datetime": datetime.datetime(2026, 8, 1, 12, 0, tzinfo=datetime.UTC),
    "date": datetime.date(2026, 8, 1),
    "url": "example",
    "badge_list": ["a", "b"],
}


def test_unknown_column_type_raises() -> None:
    with pytest.raises(ValueError, match="Unknown column type"):
        ExportColumn(key="x", title="X", type="not-a-type")


@pytest.mark.parametrize("column_type", sorted(COLUMN_TYPES))
def test_every_column_type_is_renderable_by_both_renderers(column_type: str) -> None:
    column = ExportColumn(key="value", title="Value", type=column_type, link_key="link")
    row: dict[str, object] = {"value": _SAMPLE_VALUES[column_type], "link": "https://example.test/x"}
    columns = [column]

    response = stream_csv(columns, [row, {"value": None, "link": None}], "test.csv")
    body = b"".join(response.streaming_content).decode("utf-8-sig")
    assert "Value" in body

    xlsx_bytes = write_xlsx(columns, [row, {"value": None, "link": None}], "Sheet")
    assert xlsx_bytes[:2] == b"PK"  # a well-formed zip/xlsx container


def test_every_table_spec_metric_key_is_registered() -> None:
    for spec in TABLE_SPECS.values():
        for key in spec.metric_keys:
            get_metric(key)  # raises if unknown


def test_no_table_spec_column_references_person_notes() -> None:
    for spec in TABLE_SPECS.values():
        for column in spec.columns:
            assert column.key != "notes"
            assert column.link_key != "notes"


def test_extract_value_url_column_returns_text_and_href() -> None:
    column = ExportColumn(key="name", title="Name", type="url", link_key="url")
    row = {"name": "Repo A", "url": "/repos/1/"}
    assert extract_value(column, row) == ("Repo A", "/repos/1/")


def test_extract_value_missing_key_is_none_not_zero() -> None:
    column = ExportColumn(key="prs_merged", title="Merged", type="int")
    assert extract_value(column, {}) is None


def test_delta_column_type_matches_the_metrics_own_unit_not_always_percent() -> None:
    """Round-1 BLOCKER: `<key>__delta` columns used to be hardcoded `type="percent"` while
    `rows._metric_row()` fills them from `MetricResult.delta` — an absolute difference in the
    metric's own unit, never a ratio (`MetricResult.delta_ratio` is the ratio, and has no column).
    A `prs_merged` delta of +5 rendered as "500.0%"; a `lead_time_p50` delta of -3600s rendered as
    "-360000.0%". The delta column's type must follow the value column's type."""
    for spec in TABLE_SPECS.values():
        by_key = {column.key: column for column in spec.columns}
        for metric_key in spec.metric_keys:
            value_column = by_key[metric_key]
            delta_column = by_key[f"{metric_key}__delta"]
            assert delta_column.type == value_column.type
