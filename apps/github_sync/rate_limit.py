"""Per-connection primary rate budget and the retry/backoff policy. sleep is always injected by
the caller so tests assert requested durations instead of waiting."""

import email.utils
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class RateBudget:
    key: str
    remaining: int = 5000
    reset_at: datetime | None = None
    wait_count: int = 0

    def update(self, *, remaining: int, reset_at: datetime) -> None:
        self.remaining = remaining
        self.reset_at = reset_at

    def should_wait(self, min_remaining: int) -> bool:
        return self.remaining < min_remaining

    def seconds_until_reset(self, now: datetime | None = None) -> float:
        if self.reset_at is None:
            return 0.0
        now = now or datetime.now(tz=UTC)
        return max((self.reset_at - now).total_seconds(), 0.0)

    def wait_duration(self, min_remaining: int, now: datetime | None = None) -> float:
        """Seconds to sleep before the next request, or 0 when the budget is healthy."""
        if not self.should_wait(min_remaining):
            return 0.0
        return self.seconds_until_reset(now)


@dataclass
class RateBudgetRegistry:
    """One RateBudget per rate_limit_key, held for the process lifetime of a sync run."""

    _budgets: dict[str, RateBudget] = field(default_factory=dict)

    def get(self, key: str) -> RateBudget:
        if key not in self._budgets:
            self._budgets[key] = RateBudget(key=key)
        return self._budgets[key]


def retry_after_seconds(header_value: str | None, *, now: datetime | None = None) -> float | None:
    if not header_value:
        return None
    header_value = header_value.strip()
    if header_value.isdigit():
        return float(header_value)
    try:
        retry_at = email.utils.parsedate_to_datetime(header_value)
    except (TypeError, ValueError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    now = now or datetime.now(tz=UTC)
    return max((retry_at - now).total_seconds(), 0.0)


def backoff_delays(
    max_retries: int, max_seconds: float, *, rng: "random.Random | None" = None
) -> list[float]:
    """One delay per retry attempt: exponential base capped at max_seconds, ±20% jitter,
    the jittered result clamped to max_seconds again so jitter can never push it over."""
    rng = rng or random.Random()
    delays = []
    for attempt in range(max_retries):
        base = min(2**attempt, max_seconds)
        jitter_factor = 1 + (rng.random() * 0.4 - 0.2)
        delays.append(min(base * jitter_factor, max_seconds))
    return delays
