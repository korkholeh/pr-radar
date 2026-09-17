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
