from factory.django import DjangoModelFactory

from apps.github_sync.models import SyncRun


class SyncRunFactory(DjangoModelFactory):
    class Meta:
        model = SyncRun

    trigger = SyncRun.Trigger.CLI
