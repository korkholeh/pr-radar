import datetime
from zoneinfo import ZoneInfo

from freezegun import freeze_time

from apps.metrics.timeframe import day_end_exclusive, day_start, today

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
