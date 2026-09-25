from __future__ import annotations

import datetime
from unittest import mock

import pytest

from apps.github_sync import tasks
from apps.github_sync.models import SyncRun
from apps.github_sync.services import SyncAlreadyRunning


@pytest.mark.django_db
def test_scheduled_sync_task_runs_sync_with_schedule_trigger():
    with mock.patch("apps.github_sync.tasks.run_sync") as mock_run:
        tasks.scheduled_sync_task.call_local()
    mock_run.assert_called_once_with(SyncRun.Trigger.SCHEDULE)


@pytest.mark.django_db
def test_scheduled_sync_task_skips_when_a_sync_is_running():
    with mock.patch("apps.github_sync.tasks.run_sync", side_effect=SyncAlreadyRunning):
        tasks.scheduled_sync_task.call_local()


def test_scheduled_sync_task_runs_at_the_top_of_every_hour():
    task_class = tasks.scheduled_sync_task.task_class
    instance = task_class.__new__(task_class)
    assert task_class.validate_datetime(instance, datetime.datetime(2026, 1, 1, 0, 0))
    assert task_class.validate_datetime(instance, datetime.datetime(2026, 1, 1, 13, 0))
    assert not task_class.validate_datetime(instance, datetime.datetime(2026, 1, 1, 13, 1))
    assert not task_class.validate_datetime(instance, datetime.datetime(2026, 1, 1, 13, 30))
