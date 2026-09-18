"""`stream_csv()` (plan §7): a filtered/sorted/searched table exported as CSV, every matching row
(acceptance criterion #5), CSV-injection-neutralised (acceptance criterion #6), with a UTF-8 BOM
so Excel doesn't mis-detect the encoding."""

from __future__ import annotations

import csv
import datetime
from collections.abc import Iterable, Iterator
from typing import cast
from zoneinfo import ZoneInfo

from django.conf import settings
from django.http import StreamingHttpResponse
from django.utils.timezone import localtime

from apps.dashboards.exports.columns import ExportColumn, extract_value

_DANGEROUS_PREFIXES = ("=", "+", "-", "@")


class _Echo:
    def write(self, value: str) -> str:
        return value


def _neutralise(text: str) -> str:
    """RISKS row 13 / acceptance criterion #6: a value whose first character could open a formula
    in a spreadsheet app gets an apostrophe in front of it, so it opens as literal text."""
    if text and text[0] in _DANGEROUS_PREFIXES:
        return f"'{text}"
    return text


def _format_cell(column: ExportColumn, value: object) -> str:
    if column.type == "url":
        text, _href = cast("tuple[object, object]", value)
        return _neutralise(str(text)) if text is not None else ""
    if value is None:
        return ""
    if column.type == "percent":
        return f"{cast(float, value) * 100:.1f}%"
    if column.type == "duration":
        return f"{cast(float, value) / 3600:.1f}"
    if column.type == "int":
        return str(round(cast(float, value)))
    if column.type == "float":
        return str(value)
    if column.type == "datetime":
        localized = localtime(cast(datetime.datetime, value), timezone=ZoneInfo(settings.REPORT_TIMEZONE))
        return localized.strftime("%Y-%m-%d %H:%M")
    if column.type == "date":
        return cast(datetime.date, value).strftime("%Y-%m-%d")
    if column.type == "badge_list":
        items = cast("Iterable[object]", value)
        return _neutralise(", ".join(str(item) for item in items)) if value else ""
    return _neutralise(str(value))


def _iter_csv_rows(columns: Iterable[ExportColumn], data_rows: Iterable[dict[str, object]]) -> Iterator[str]:
    writer = csv.writer(_Echo())
    yield "﻿"
    yield writer.writerow([str(column.title) for column in columns])
    for row in data_rows:
        yield writer.writerow([_format_cell(column, extract_value(column, row)) for column in columns])


def stream_csv(
    columns: Iterable[ExportColumn], data_rows: Iterable[dict[str, object]], filename: str
) -> StreamingHttpResponse:
    response = StreamingHttpResponse(
        _iter_csv_rows(columns, data_rows), content_type="text/csv; charset=utf-8"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def render_csv_bytes(columns: Iterable[ExportColumn], data_rows: Iterable[dict[str, object]]) -> bytes:
    """Same rows/formatting as `stream_csv()`, materialised as `bytes` instead of a streaming
    response — for a background export job (T22), which writes the file directly rather than
    streaming it in a request."""
    return "".join(_iter_csv_rows(columns, data_rows)).encode("utf-8")
