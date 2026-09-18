import datetime
from zoneinfo import ZoneInfo

from freezegun import freeze_time

from apps.metrics.timeframe import (
    bucket_ranges,
    date_range,
    day_end_exclusive,
    day_of,
    day_start,
    previous_period,
    today,
)

KYIV = ZoneInfo("Europe/Kyiv")


def test_day_start_winter_maps_to_right_utc_instant():
    start = day_start(datetime.date(2026, 1, 15))
    assert start == datetime.datetime(2026, 1, 15, 0, 0, tzinfo=KYIV)
    assert start.astimezone(datetime.UTC) == datetime.datetime(2026, 1, 14, 22, 0, tzinfo=datetime.UTC)


def test_day_start_summer_maps_to_right_utc_instant():
    start = day_start(datetime.date(2026, 7, 15))
    assert start == datetime.datetime(2026, 7, 15, 0, 0, tzinfo=KYIV)
    assert start.astimezone(datetime.UTC) == datetime.datetime(2026, 7, 14, 21, 0, tzinfo=datetime.UTC)


def test_day_start_handles_dst_transition_day():
    # Europe/Kyiv springs forward on 2026-03-29 (EET +2 -> EEST +3). A UTC-naive "always +2/+3"
    # helper would place both midnights 24h apart in absolute time; the actual gap is 23h because
    # the transition falls inside that calendar day. Comparing in UTC (not by direct subtraction
    # of two aware zoneinfo datetimes, which silently ignores a DST-offset change between them)
    # proves the helper resolves each day's midnight against its own real UTC offset.
    before_utc = day_start(datetime.date(2026, 3, 29)).astimezone(datetime.UTC)
    after_utc = day_start(datetime.date(2026, 3, 30)).astimezone(datetime.UTC)
    assert after_utc - before_utc == datetime.timedelta(hours=23)


def test_day_end_exclusive_is_next_days_start():
    date = datetime.date(2026, 5, 1)
    assert day_end_exclusive(date) == day_start(date + datetime.timedelta(days=1))


def test_accepts_iso_string():
    assert day_start("2026-01-15") == day_start(datetime.date(2026, 1, 15))
    assert day_end_exclusive("2026-01-15") == day_end_exclusive(datetime.date(2026, 1, 15))


def test_today_uses_report_timezone_not_the_process_timezone():
    # settings.TIME_ZONE is UTC (see config/settings/base.py); at 23:11 UTC, Kyiv (UTC+3) has
    # already turned over to the next calendar day. `django.utils.timezone.localdate()` would
    # still report 2026-09-17 here -- exactly the bug this helper exists to avoid (a real
    # regression found by e2e/plans/policy_console.plan.yaml: "new violations" and "violations by
    # rule" silently excluded every row created in this window because apps/policy/views.py's
    # `_period()` used to call `timezone.localdate()` directly).
    with freeze_time("2026-09-17 23:11:00+00:00"):
        assert today() == datetime.date(2026, 9, 18)


def test_day_of_splits_kyiv_midnight():
    just_before_midnight_kyiv = datetime.datetime(2026, 1, 15, 23, 59, tzinfo=KYIV)
    just_after_midnight_kyiv = datetime.datetime(2026, 1, 16, 0, 1, tzinfo=KYIV)
    assert day_of(just_before_midnight_kyiv) == datetime.date(2026, 1, 15)
    assert day_of(just_after_midnight_kyiv) == datetime.date(2026, 1, 16)
    # The same instants read back from UTC storage must land on the same Kyiv days.
    assert day_of(just_before_midnight_kyiv.astimezone(datetime.UTC)) == datetime.date(2026, 1, 15)
    assert day_of(just_after_midnight_kyiv.astimezone(datetime.UTC)) == datetime.date(2026, 1, 16)


def test_day_of_on_the_dst_transition_day():
    before_transition = datetime.datetime(2026, 3, 29, 2, 59, tzinfo=KYIV)
    assert day_of(before_transition.astimezone(datetime.UTC)) == datetime.date(2026, 3, 29)
    late_same_day = datetime.datetime(2026, 3, 29, 23, 30, tzinfo=KYIV)
    assert day_of(late_same_day.astimezone(datetime.UTC)) == datetime.date(2026, 3, 29)


def test_previous_period_one_day_window():
    assert previous_period(datetime.date(2026, 5, 10), datetime.date(2026, 5, 10)) == (
        datetime.date(2026, 5, 9),
        datetime.date(2026, 5, 9),
    )


def test_previous_period_thirty_day_window():
    assert previous_period(datetime.date(2026, 5, 1), datetime.date(2026, 5, 30)) == (
        datetime.date(2026, 4, 1),
        datetime.date(2026, 4, 30),
    )


def test_previous_period_accepts_iso_strings():
    assert previous_period("2026-05-10", "2026-05-10") == (
        datetime.date(2026, 5, 9),
        datetime.date(2026, 5, 9),
    )


def test_date_range_is_inclusive():
    assert date_range(datetime.date(2026, 1, 1), datetime.date(2026, 1, 3)) == [
        datetime.date(2026, 1, 1),
        datetime.date(2026, 1, 2),
        datetime.date(2026, 1, 3),
    ]


def test_bucket_ranges_day_granularity():
    buckets = bucket_ranges(datetime.date(2026, 1, 1), datetime.date(2026, 1, 3), "day")
    assert buckets == [
        (datetime.date(2026, 1, 1), datetime.date(2026, 1, 1)),
        (datetime.date(2026, 1, 2), datetime.date(2026, 1, 2)),
        (datetime.date(2026, 1, 3), datetime.date(2026, 1, 3)),
    ]


def test_bucket_ranges_week_granularity_with_partial_trailing_bucket():
    # 2026-01-01 is a Thursday; the ISO week containing it is 2025-12-29..2026-01-04, clipped at
    # the front by date_from, and the range ends mid-week on 2026-01-10 (a Saturday), clipped at
    # the back.
    buckets = bucket_ranges(datetime.date(2026, 1, 1), datetime.date(2026, 1, 10), "week")
    assert buckets == [
        (datetime.date(2026, 1, 1), datetime.date(2026, 1, 4)),
        (datetime.date(2026, 1, 5), datetime.date(2026, 1, 10)),
    ]


def test_bucket_ranges_month_granularity_with_partial_leading_and_trailing_bucket():
    buckets = bucket_ranges(datetime.date(2026, 1, 15), datetime.date(2026, 3, 10), "month")
    assert buckets == [
        (datetime.date(2026, 1, 15), datetime.date(2026, 1, 31)),
        (datetime.date(2026, 2, 1), datetime.date(2026, 2, 28)),
        (datetime.date(2026, 3, 1), datetime.date(2026, 3, 10)),
    ]


def test_bucket_ranges_rejects_unknown_granularity():
    import pytest

    with pytest.raises(ValueError):
        bucket_ranges(datetime.date(2026, 1, 1), datetime.date(2026, 1, 2), "year")
