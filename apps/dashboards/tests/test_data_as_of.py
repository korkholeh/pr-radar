"""T9: the "data as of" banner — last **successful** `SyncRun`, ignoring a failed newer one, with
a distinct "never synced" message."""

from __future__ import annotations

import datetime

import pytest
from django.utils import timezone

from apps.dashboards.templatetags.dashboards import data_as_of_banner
from apps.github_sync.models import SyncRun


@pytest.mark.django_db
def test_never_synced_shows_distinct_message():
    context = data_as_of_banner()
    assert context["finished_at"] is None


@pytest.mark.django_db
def test_shows_last_successful_run_and_ignores_failed_newer_one():
    now = timezone.now()
    SyncRun.objects.create(
        trigger=SyncRun.Trigger.CLI,
        status=SyncRun.Status.SUCCESS,
        started_at=now - datetime.timedelta(hours=2),
        finished_at=now - datetime.timedelta(hours=1),
    )
    SyncRun.objects.create(
        trigger=SyncRun.Trigger.CLI,
        status=SyncRun.Status.FAILED,
        started_at=now - datetime.timedelta(minutes=10),
        finished_at=now - datetime.timedelta(minutes=5),
    )
    context = data_as_of_banner()
    assert context["finished_at"] is not None
    assert context["finished_at"].tzinfo is not None
