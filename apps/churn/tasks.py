"""Nightly churn computation (ADR 0005: every huey task is also a management command --
`manage.py compute_churn`). Runs at 02:00, an hour before `cleanup_exports_task` at 03:00."""

from huey import crontab
from huey.contrib.djhuey import db_periodic_task

from apps.churn.services import run_churn


@db_periodic_task(crontab(hour="2", minute="0"))
def compute_churn_task() -> None:
    run_churn()
