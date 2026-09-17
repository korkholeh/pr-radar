import factory
from factory.django import DjangoModelFactory

from apps.metrics.models import Cohort, DailyRollup, ScopeType


class DailyRollupFactory(DjangoModelFactory):
    class Meta:
        model = DailyRollup

    date = factory.Sequence(lambda n: f"2026-01-{(n % 27) + 1:02d}")
    scope_type = ScopeType.GLOBAL
    cohort = Cohort.ALL
    metric_key = factory.Sequence(lambda n: f"metric-{n}")
