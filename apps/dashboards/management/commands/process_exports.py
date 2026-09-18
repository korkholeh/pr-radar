from django.core.management.base import BaseCommand

from apps.dashboards.models import ExportJob
from apps.dashboards.services import run_export_job


class Command(BaseCommand):
    help = "Runs every pending export job inline (ADR 0005) — for when no huey worker is running."

    def handle(self, *args, **options) -> None:
        job_ids = list(ExportJob.objects.filter(status=ExportJob.Status.PENDING).values_list("id", flat=True))
        for job_id in job_ids:
            run_export_job(job_id)
        self.stdout.write(f"Processed {len(job_ids)} export job(s).")
