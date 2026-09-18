from django.core.management.base import BaseCommand

from apps.dashboards.services import cleanup_exports


class Command(BaseCommand):
    help = "Deletes expired export files/rows and sweeps stuck RUNNING jobs to FAILED (ADR 0005)."

    def handle(self, *args, **options) -> None:
        result = cleanup_exports()
        self.stdout.write(
            f"Deleted {result.deleted} expired export(s); swept {result.stuck_swept} stuck job(s)."
        )
