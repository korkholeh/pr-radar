import datetime

import pytest

from apps.metrics.calculators.base import breakdown_value, duration_hours, median, percentile, ratio_value
from apps.metrics.types import MetricValue

pytestmark = pytest.mark.django_db

UTC = datetime.UTC


def test_median_even_sample():
    # [1, 2, 3, 4] -> average of the two middle values.
    assert median([1, 2, 3, 4]) == MetricValue(2.5, 4)


def test_median_odd_sample():
    assert median([1, 2, 3]) == MetricValue(2, 3)


def test_percentile_p90_hand_computed():
    # rank = 0.9 * 9 = 8.1 -> interpolate between the 9th (index 8, value 9) and 10th
    # (index 9, value 10) sorted values: 9 * 0.9 + 10 * 0.1 = 9.1.
    values = list(range(1, 11))
    result = percentile(values, 90)
    assert result.sample_size == 10
    assert round(result.value, 4) == 9.1


def test_nones_are_dropped_from_value_and_sample_size():
    result = median([1, None, 2, None, 3])
    assert result == MetricValue(2, 3)


def test_empty_sample_is_none_not_zero():
    assert median([]) == MetricValue.empty()
    assert median([None, None]) == MetricValue.empty()
    assert percentile([], 90) == MetricValue.empty()


def test_single_value_sample():
    assert median([7]) == MetricValue(7, 1)


def test_duration_hours_happy_path():
    start = datetime.datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    end = datetime.datetime(2026, 1, 1, 6, 0, tzinfo=UTC)
    assert duration_hours(start, end) == 6.0


def test_duration_hours_drops_negative_duration():
    # Bad data: merged before marked ready.
    ready = datetime.datetime(2026, 1, 2, 0, 0, tzinfo=UTC)
    merged = datetime.datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    assert duration_hours(merged, ready) == 24.0
    assert duration_hours(ready, merged) is None


def test_duration_hours_missing_bound_is_none():
    now = datetime.datetime(2026, 1, 1, tzinfo=UTC)
    assert duration_hours(None, now) is None
    assert duration_hours(now, None) is None


def test_ratio_value_divides_and_carries_denominator_sample_size():
    numerator = MetricValue(3, 3)
    denominator = MetricValue(10, 10)
    assert ratio_value(numerator, denominator) == MetricValue(0.3, 10)


def test_ratio_value_is_none_when_denominator_is_empty_or_zero():
    assert ratio_value(MetricValue.empty(), MetricValue.empty()) == MetricValue(None, 0)
    assert ratio_value(MetricValue(0, 5), MetricValue(0, 5)) == MetricValue(None, 5)


def test_ratio_value_treats_a_none_numerator_as_zero_when_denominator_has_data():
    # No numerator rollup rows in the period (nothing matched) but a real denominator: 0/10, not unknown.
    assert ratio_value(MetricValue.empty(), MetricValue(10, 10)) == MetricValue(0.0, 10)


def test_breakdown_value_drops_zero_count_labels_and_sorts_by_count():
    items = breakdown_value({"claude_code": 3, "copilot": 5, "cursor": 0})
    assert [item.label for item in items] == ["copilot", "claude_code"]
    assert items[0].value == 5.0
    assert items[0].sample_size == 8
