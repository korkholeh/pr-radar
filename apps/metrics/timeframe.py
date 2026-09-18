"""The one place that turns a calendar date into a UTC instant (spec: day boundaries, rollups and
reports use `REPORT_TIMEZONE`, storage is UTC). `manage.py recompute` and `policy/selectors.py`
both need "the Kyiv day this row falls in" and must agree, so this is the single helper both call."""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

from django.conf import settings


def _as_date(date: datetime.date | str) -> datetime.date:
    """`call_command(..., **{"from": "2026-01-01"})` bypasses argparse's `type=` conversion, so
    `date` may still be an ISO string here."""
    return datetime.date.fromisoformat(date) if isinstance(date, str) else date


def day_start(date: datetime.date | str) -> datetime.datetime:
    """Midnight of `date` in `REPORT_TIMEZONE`, as a UTC-aware instant."""
    return datetime.datetime.combine(
        _as_date(date), datetime.time.min, tzinfo=ZoneInfo(settings.REPORT_TIMEZONE)
    )


def day_end_exclusive(date: datetime.date | str) -> datetime.datetime:
    """Midnight of the day after `date` in `REPORT_TIMEZONE` — the exclusive upper bound of a
    closed-open day range starting at `day_start(date)`."""
    return day_start(_as_date(date) + datetime.timedelta(days=1))


def today() -> datetime.date:
    """ "Today" in `REPORT_TIMEZONE`, not `django.utils.timezone.localdate()` (which uses
    `settings.TIME_ZONE`, UTC here — for several hours every evening Kyiv has already turned over
    to the next calendar day while `localdate()` still reports the previous one, silently dropping
    same-day rows from the tail of a "last N days" report window)."""
    return datetime.datetime.now(ZoneInfo(settings.REPORT_TIMEZONE)).date()


def day_of(instant: datetime.datetime) -> datetime.date:
    """The `REPORT_TIMEZONE` calendar day a stored UTC instant falls in — the inverse of
    `day_start`/`day_end_exclusive`. A PR merged at 23:59 Kyiv is stored a few hours earlier in
    UTC and must still land on that Kyiv day, not the UTC one."""
    return instant.astimezone(ZoneInfo(settings.REPORT_TIMEZONE)).date()


def previous_period(
    date_from: datetime.date | str, date_to: datetime.date | str
) -> tuple[datetime.date, datetime.date]:
    """The period immediately before `[date_from, date_to]`, of the same length (both bounds
    inclusive) — the single definition `compute()`'s delta/`delta_ratio` is measured against."""
    date_from = _as_date(date_from)
    date_to = _as_date(date_to)
    length = (date_to - date_from).days + 1
    previous_to = date_from - datetime.timedelta(days=1)
    previous_from = previous_to - datetime.timedelta(days=length - 1)
    return previous_from, previous_to


def date_range(date_from: datetime.date | str, date_to: datetime.date | str) -> list[datetime.date]:
    """Every calendar date from `date_from` to `date_to`, inclusive."""
    date_from = _as_date(date_from)
    date_to = _as_date(date_to)
    days = (date_to - date_from).days
    return [date_from + datetime.timedelta(days=offset) for offset in range(days + 1)]


def bucket_ranges(
    date_from: datetime.date | str, date_to: datetime.date | str, granularity: str
) -> list[tuple[datetime.date, datetime.date]]:
    """Splits `[date_from, date_to]` into inclusive `(start, end)` buckets for a series, clipped to
    the requested range (the first/last bucket may be partial). `week` buckets are ISO weeks
    (Monday start); `month` buckets are calendar months. All boundaries are Kyiv calendar dates —
    the same ones `day_start`/`day_end_exclusive` turn into UTC instants at read time."""
    date_from = _as_date(date_from)
    date_to = _as_date(date_to)
    if granularity == "day":
        return [(day, day) for day in date_range(date_from, date_to)]

    if granularity == "week":
        bucket_start = date_from - datetime.timedelta(days=date_from.weekday())
        step = datetime.timedelta(days=7)
    elif granularity == "month":
        bucket_start = date_from.replace(day=1)
        step = None  # computed per-bucket below
    else:
        raise ValueError(f"Unknown granularity {granularity!r}.")

    buckets: list[tuple[datetime.date, datetime.date]] = []
    while bucket_start <= date_to:
        if granularity == "week":
            bucket_end = bucket_start + datetime.timedelta(days=6)
            next_start = bucket_start + step  # type: ignore[operator]
        else:
            if bucket_start.month == 12:
                next_start = bucket_start.replace(year=bucket_start.year + 1, month=1)
            else:
                next_start = bucket_start.replace(month=bucket_start.month + 1)
            bucket_end = next_start - datetime.timedelta(days=1)
        buckets.append((max(bucket_start, date_from), min(bucket_end, date_to)))
        bucket_start = next_start
    return buckets
