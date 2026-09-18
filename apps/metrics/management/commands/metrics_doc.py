from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.metrics.docs import render_metrics_doc

METRICS_DOC_PATH = Path(settings.BASE_DIR) / "docs" / "METRICS.md"


class Command(BaseCommand):
    help = "Regenerates docs/METRICS.md from the live metric registry (apps/metrics/registry.py)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--check",
            action="store_true",
            help="Exit non-zero if docs/METRICS.md is stale, without writing it.",
        )

    def handle(self, *args, **options) -> None:
        rendered = render_metrics_doc()

        if options["check"]:
            committed = METRICS_DOC_PATH.read_text(encoding="utf-8") if METRICS_DOC_PATH.exists() else None
            if committed != rendered:
                self.stderr.write("docs/METRICS.md is stale — run `manage.py metrics_doc` and commit it.")
                raise SystemExit(1)
            self.stdout.write("docs/METRICS.md is up to date.")
            return

        METRICS_DOC_PATH.write_text(rendered, encoding="utf-8")
        self.stdout.write(f"Wrote {METRICS_DOC_PATH}.")
