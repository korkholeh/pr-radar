import factory
from factory.django import DjangoModelFactory

from apps.metrics.models import Cohort, DailyRollup, DataVersion, DirtyDay, ScopeType


class DailyRollupFactory(DjangoModelFactory):
    class Meta:
        model = DailyRollup

    date = factory.Sequence(lambda n: f"2026-01-{(n % 27) + 1:02d}")
    scope_type = ScopeType.GLOBAL
    cohort = Cohort.ALL
    metric_key = factory.Sequence(lambda n: f"metric-{n}")


class DataVersionFactory(DjangoModelFactory):
    class Meta:
        model = DataVersion

    version = 1


class DirtyDayFactory(DjangoModelFactory):
    class Meta:
        model = DirtyDay

    date = factory.Sequence(lambda n: f"2026-01-{(n % 27) + 1:02d}")
