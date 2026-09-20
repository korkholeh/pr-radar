import datetime

from django.core.management.base import BaseCommand

from apps.ai_detection.services import run_baselines


class Command(BaseCommand):
    help = (
        "Recomputes the author-baseline structural signals over the rolling window and reconciles "
        "the stored ones against the result (ADR 0005: the nightly compute_baselines_task runs "
        "this same function). Touches only the baseline signal family, and makes no GitHub call."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--as-of",
            dest="reference",
            type=datetime.date.fromisoformat,
            help="Treat this ISO date as the end of the window instead of today, for inspecting a "
            "past period. It reconciles as usual, so signals outside the shifted window are "
            "deleted: run the command again without --as-of afterwards to restore the live set.",
        )

    def handle(self, *args, **options) -> None:
        result = run_baselines(reference=options.get("reference"))
        self.stdout.write(
            f"Baseline signals: {result.authors} author(s), {result.created} created, "
            f"{result.deleted} deleted, {result.pull_requests_restatused} pull request(s) restatused."
        )
