import random
from datetime import UTC, datetime, timedelta

import freezegun
import pytest

from apps.github_sync.rate_limit import RateBudget, RateBudgetRegistry, backoff_delays, retry_after_seconds


@freezegun.freeze_time("2026-01-01T00:00:00Z")
def test_low_remaining_requests_a_sleep_until_reset_at():
    now = datetime.now(tz=UTC)
    reset_at = now + timedelta(seconds=90)
    budget = RateBudget(key="connection:1")
    budget.update(remaining=150, reset_at=reset_at)

    assert budget.should_wait(200) is True
    assert budget.wait_duration(200) == pytest.approx(90, abs=1)


@freezegun.freeze_time("2026-01-01T00:00:00Z")
def test_healthy_remaining_requests_no_sleep():
    now = datetime.now(tz=UTC)
    budget = RateBudget(key="connection:1")
    budget.update(remaining=4900, reset_at=now + timedelta(seconds=90))

    assert budget.should_wait(200) is False
    assert budget.wait_duration(200) == 0.0


def test_two_budgets_with_different_keys_are_independent():
    registry = RateBudgetRegistry()
    budget_a = registry.get("connection:1")
    budget_b = registry.get("connection:2")
    budget_a.update(remaining=10, reset_at=datetime.now(tz=UTC))

    assert budget_a.should_wait(200) is True
    assert budget_b.should_wait(200) is False
    assert registry.get("connection:1") is budget_a


def test_retry_after_seconds_is_honoured_exactly():
    assert retry_after_seconds("30") == 30.0


@freezegun.freeze_time("2026-01-01T00:00:00Z")
def test_retry_after_http_date_is_honoured():
    now = datetime.now(tz=UTC)
    future = now + timedelta(seconds=45)
    header = email_format(future)
    assert retry_after_seconds(header) == pytest.approx(45, abs=1)


def email_format(dt: datetime) -> str:
    import email.utils

    return email.utils.format_datetime(dt, usegmt=True)


def test_retry_after_absent_returns_none():
    assert retry_after_seconds(None) is None
    assert retry_after_seconds("") is None


def test_retry_after_zero_is_honoured():
    assert retry_after_seconds("0") == 0.0


def test_backoff_delays_never_exceed_max_seconds():
    delays = backoff_delays(5, 60, rng=random.Random(1))
    assert all(0 <= d <= 60 for d in delays)
    assert len(delays) == 5


def test_backoff_delays_are_exponential_before_the_cap():
    delays = backoff_delays(3, 1000, rng=random.Random(0))
    # jitter is +/-20%, so each delay should be roughly double the previous one
    assert delays[1] > delays[0]
    assert delays[2] > delays[1]
