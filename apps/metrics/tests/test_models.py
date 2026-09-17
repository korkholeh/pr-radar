import pytest
from django.db import IntegrityError, transaction

from apps.metrics.models import Cohort, DailyRollup, ScopeType


@pytest.mark.django_db
def test_duplicate_rollup_rejected():
    DailyRollup.objects.create(
        date="2026-01-01",
        scope_type=ScopeType.PROJECT,
        scope_id=1,
        cohort=Cohort.ALL,
        metric_key="cycle_time",
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        DailyRollup.objects.create(
            date="2026-01-01",
            scope_type=ScopeType.PROJECT,
            scope_id=1,
            cohort=Cohort.ALL,
            metric_key="cycle_time",
        )


@pytest.mark.django_db
def test_duplicate_global_rollup_rejected():
    DailyRollup.objects.create(
        date="2026-01-01",
        scope_type=ScopeType.GLOBAL,
        scope_id=None,
        cohort=Cohort.ALL,
        metric_key="cycle_time",
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        DailyRollup.objects.create(
            date="2026-01-01",
            scope_type=ScopeType.GLOBAL,
            scope_id=None,
            cohort=Cohort.ALL,
            metric_key="cycle_time",
        )


@pytest.mark.django_db
def test_same_metric_different_cohort_date_scope_coexist():
    DailyRollup.objects.create(
        date="2026-01-01", scope_type=ScopeType.PROJECT, scope_id=1, cohort=Cohort.AI, metric_key="cycle_time"
    )
    DailyRollup.objects.create(
        date="2026-01-01",
        scope_type=ScopeType.PROJECT,
        scope_id=1,
        cohort=Cohort.NON_AI,
        metric_key="cycle_time",
    )
    DailyRollup.objects.create(
        date="2026-01-02", scope_type=ScopeType.PROJECT, scope_id=1, cohort=Cohort.AI, metric_key="cycle_time"
    )
    DailyRollup.objects.create(
        date="2026-01-01", scope_type=ScopeType.PROJECT, scope_id=2, cohort=Cohort.AI, metric_key="cycle_time"
    )
    assert DailyRollup.objects.count() == 4


@pytest.mark.django_db
def test_value_none_is_storable_and_distinct_from_zero():
    none_row = DailyRollup.objects.create(
        date="2026-01-01", scope_type=ScopeType.PROJECT, scope_id=1, cohort=Cohort.ALL, metric_key="a"
    )
    zero_row = DailyRollup.objects.create(
        date="2026-01-01",
        scope_type=ScopeType.PROJECT,
        scope_id=1,
        cohort=Cohort.ALL,
        metric_key="b",
        value=0.0,
    )
    assert none_row.value is None
    assert zero_row.value == 0.0
