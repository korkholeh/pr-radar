import factory
from factory.django import DjangoModelFactory

from apps.github_sync.models import SyncLock, SyncRun


class SyncRunFactory(DjangoModelFactory):
    class Meta:
        model = SyncRun

    trigger = SyncRun.Trigger.CLI


class SyncLockFactory(DjangoModelFactory):
    class Meta:
        model = SyncLock
        django_get_or_create = ("name",)

    name = factory.Sequence(lambda n: f"lock-{n}")
    sync_run = factory.SubFactory(SyncRunFactory)
