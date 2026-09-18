from __future__ import annotations

import datetime
from unittest import mock

import pytest

from apps.churn import tasks


@pytest.mark.django_db
def test_compute_churn_task_calls_run_churn():
    with mock.patch("apps.churn.tasks.run_churn") as mock_run:
        tasks.compute_churn_task.call_local()
    mock_run.assert_called_once_with()


def test_compute_churn_task_is_scheduled_at_02_00():
    task_class = tasks.compute_churn_task.task_class
    instance = task_class.__new__(task_class)
    assert task_class.validate_datetime(instance, datetime.datetime(2026, 1, 1, 2, 0))
    assert not task_class.validate_datetime(instance, datetime.datetime(2026, 1, 1, 2, 1))
    assert not task_class.validate_datetime(instance, datetime.datetime(2026, 1, 1, 3, 0))
