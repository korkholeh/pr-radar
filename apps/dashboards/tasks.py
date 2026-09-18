"""Background export tasks (plan §6, ADR 0005: every huey task is also a management command —
`manage.py process_exports`/`manage.py cleanup_exports` and these tasks both call
`apps.dashboards.services`)."""

from huey import crontab
from huey.contrib.djhuey import db_periodic_task, db_task

from apps.dashboards.services import cleanup_exports, run_export_job


@db_task()
def export_job_task(job_id: int) -> None:
    run_export_job(job_id)


@db_periodic_task(crontab(hour="3", minute="0"))
def cleanup_exports_task() -> None:
    cleanup_exports()
