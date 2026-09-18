"""T15: `exports/csv.py::stream_csv()`."""

from __future__ import annotations

import csv
import io

from apps.dashboards.exports.columns import ExportColumn
from apps.dashboards.exports.csv import stream_csv


def _read_rows(response) -> list[list[str]]:
    body = b"".join(response.streaming_content).decode("utf-8-sig")
    return list(csv.reader(io.StringIO(body)))


def test_formula_like_values_are_neutralised() -> None:
    """Acceptance criterion #6: `=`, `+`, `-`, `@` are apostrophe-prefixed."""
    column = ExportColumn(key="title", title="Title", type="text")
    rows = [
        {"title": "=cmd|'/c calc'!A1"},
        {"title": "+1234"},
        {"title": "-1234"},
        {"title": "@mention"},
        {"title": "safe value"},
    ]
    response = stream_csv([column], rows, "test.csv")
    body_rows = _read_rows(response)
    values = [row[0] for row in body_rows[1:] if row]
    assert values[0] == "'=cmd|'/c calc'!A1"
    assert values[1] == "'+1234"
    assert values[2] == "'-1234"
    assert values[3] == "'@mention"
    assert values[4] == "safe value"


def test_none_is_an_empty_field() -> None:
    column = ExportColumn(key="value", title="Value", type="int")
    response = stream_csv([column], [{"value": None}], "test.csv")
    body_rows = _read_rows(response)
    assert body_rows[1] == [""]


def test_url_column_neutralises_the_display_text() -> None:
    column = ExportColumn(key="name", title="Name", type="url", link_key="url")
    response = stream_csv([column], [{"name": "=EVIL()", "url": "/x/"}], "test.csv")
    body_rows = _read_rows(response)
    assert body_rows[1] == ["'=EVIL()"]


def test_percent_and_duration_formatting() -> None:
    columns = [
        ExportColumn(key="ratio", title="Ratio", type="percent"),
        ExportColumn(key="dur", title="Duration", type="duration"),
    ]
    response = stream_csv(columns, [{"ratio": 0.4567, "dur": 7200.0}], "test.csv")
    body_rows = _read_rows(response)
    assert body_rows[1] == ["45.7%", "2.0"]


def test_delta_cells_render_in_their_own_unit_not_as_a_percent() -> None:
    """Round-1 BLOCKER regression: a `prs_merged__delta` of +5.0 (an `int`-typed column, per the
    fix to `exports/columns.py::_metric_columns`) must render "5", not "500.0%"; a
    `lead_time_p50__delta` of -3600.0 (`duration`-typed) must render "-1.0" hours, not
    "-360000.0%"."""
    columns = [
        ExportColumn(key="prs_merged__delta", title="PRs merged (Δ)", type="int"),
        ExportColumn(key="lead_time_p50__delta", title="Lead time (Δ)", type="duration"),
    ]
    response = stream_csv(columns, [{"prs_merged__delta": 5.0, "lead_time_p50__delta": -3600.0}], "test.csv")
    body_rows = _read_rows(response)
    assert body_rows[1] == ["5", "-1.0"]


def test_response_is_a_streaming_attachment_with_ascii_filename() -> None:
    column = ExportColumn(key="value", title="Value", type="text")
    response = stream_csv([column], [{"value": "x"}], "pr-radar_projects_global_2026-08-01_2026-08-31.csv")
    assert response.streaming
    disposition = response["Content-Disposition"]
    assert disposition == 'attachment; filename="pr-radar_projects_global_2026-08-01_2026-08-31.csv"'
    disposition.encode("ascii")  # raises if a non-ASCII character slipped in
