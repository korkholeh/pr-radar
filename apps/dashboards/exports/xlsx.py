"""`write_xlsx()` (plan §7, spec §10.6, acceptance criterion #7): bold header row, frozen header,
an autofilter over the used range, numbers written with `write_number` (never as strings),
percentages as `0.0%`, durations as hours with a ", h" column title, a clickable PR hyperlink,
`None` as an empty cell."""

from __future__ import annotations

import io
from zoneinfo import ZoneInfo

import xlsxwriter
from django.conf import settings
from django.utils.timezone import localtime

from apps.dashboards.exports.columns import ExportColumn, extract_value

_MAX_COLUMN_WIDTH = 60
_INVALID_SHEET_NAME_CHARS = frozenset("[]:*?/\\")

# Spec §10.6's "conditional formatting of deltas (green/red by direction)" — a light palette,
# independent of the reader's web UI theme, since an .xlsx has no notion of CSS custom properties
# (tests/test_no_hardcoded_colors.py exempts only these two lines by their `color-literal-exempt`
# marker, not this whole file — round 2 audit MINOR #3). Applied only to a column carrying
# `ExportColumn.direction` (a delta column).
_DELTA_GOOD_FORMAT = {"bg_color": "#E6F4EA", "font_color": "#1B7F3B"}  # color-literal-exempt
_DELTA_BAD_FORMAT = {"bg_color": "#FBE9E7", "font_color": "#C0392B"}  # color-literal-exempt
_DELTA_NUM_FORMATS: dict[str, str | None] = {
    "percent": "0.0%",
    "duration": "0.0",
    "int": "0",
    "float": None,
}


def _delta_color(direction: str, value: float) -> str | None:
    """`None` for a neutral metric or a zero delta (nothing changed, nothing to colour); otherwise
    green for a delta the metric's own `direction` calls an improvement, red for the opposite."""
    if direction == "neutral" or value == 0:
        return None
    improved = value > 0 if direction == "higher_is_better" else value < 0
    return "good" if improved else "bad"


def _clean_sheet_name(name: str) -> str:
    cleaned = "".join(char for char in name if char not in _INVALID_SHEET_NAME_CHARS)
    return cleaned[:31] or "Sheet1"


def _column_title(column: ExportColumn) -> str:
    title = str(column.title)
    return f"{title}, h" if column.type == "duration" else title


def write_xlsx(columns: list[ExportColumn], data_rows: list[dict[str, object]], sheet_name: str) -> bytes:
    buffer = io.BytesIO()
    workbook = xlsxwriter.Workbook(
        buffer,
        {
            "in_memory": True,
            "constant_memory": True,
            "strings_to_formulas": False,
            "strings_to_urls": False,
        },
    )
    worksheet = workbook.add_worksheet(_clean_sheet_name(sheet_name))

    formats = {
        "header": workbook.add_format({"bold": True}),
        "percent": workbook.add_format({"num_format": "0.0%"}),
        "duration": workbook.add_format({"num_format": "0.0"}),
        "int": workbook.add_format({"num_format": "0"}),
        "datetime": workbook.add_format({"num_format": "yyyy-mm-dd hh:mm"}),
        "date": workbook.add_format({"num_format": "yyyy-mm-dd"}),
    }
    for value_type, num_format in _DELTA_NUM_FORMATS.items():
        for kind, palette in (("good", _DELTA_GOOD_FORMAT), ("bad", _DELTA_BAD_FORMAT)):
            spec = dict(palette)
            if num_format is not None:
                spec["num_format"] = num_format
            formats[f"delta_{value_type}_{kind}"] = workbook.add_format(spec)

    for col_index, column in enumerate(columns):
        worksheet.write_string(0, col_index, _column_title(column), formats["header"])
        worksheet.set_column(col_index, col_index, min(column.width, _MAX_COLUMN_WIDTH))

    row_index = 0
    for row_index, row in enumerate(data_rows, start=1):
        for col_index, column in enumerate(columns):
            value = extract_value(column, row)
            _write_value(worksheet, row_index, col_index, column, value, formats)

    worksheet.freeze_panes(1, 0)
    if columns:
        worksheet.autofilter(0, 0, row_index, len(columns) - 1)

    workbook.close()
    return buffer.getvalue()


def _write_value(worksheet, row_index: int, col_index: int, column: ExportColumn, value, formats) -> None:
    if column.type == "url":
        text, href = value
        if href:
            display = str(text) if text is not None else str(href)
            worksheet.write_url(row_index, col_index, str(href), string=display)
        else:
            worksheet.write_blank(row_index, col_index, None)
        return
    if value is None:
        worksheet.write_blank(row_index, col_index, None)
        return
    delta_color = _delta_color(column.direction, value) if column.direction else None
    if column.type == "percent":
        fmt = formats[f"delta_percent_{delta_color}"] if delta_color else formats["percent"]
        worksheet.write_number(row_index, col_index, value, fmt)
        return
    if column.type == "duration":
        fmt = formats[f"delta_duration_{delta_color}"] if delta_color else formats["duration"]
        worksheet.write_number(row_index, col_index, value / 3600, fmt)
        return
    if column.type == "int":
        fmt = formats[f"delta_int_{delta_color}"] if delta_color else formats["int"]
        worksheet.write_number(row_index, col_index, round(value), fmt)
        return
    if column.type == "float":
        fmt = formats[f"delta_float_{delta_color}"] if delta_color else None
        worksheet.write_number(row_index, col_index, value, fmt)
        return
    if column.type == "datetime":
        localized = localtime(value, timezone=ZoneInfo(settings.REPORT_TIMEZONE)).replace(tzinfo=None)
        worksheet.write_datetime(row_index, col_index, localized, formats["datetime"])
        return
    if column.type == "date":
        worksheet.write_datetime(row_index, col_index, value, formats["date"])
        return
    if column.type == "badge_list":
        worksheet.write_string(row_index, col_index, ", ".join(str(item) for item in value) if value else "")
        return
    worksheet.write_string(row_index, col_index, str(value))
