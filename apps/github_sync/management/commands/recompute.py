import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db.models.functions import Coalesce

from apps.activity.derive import derive_pull_requests
from apps.activity.followup import update_followup_fixes_for
from apps.activity.models import PullRequest
from apps.ai_detection.services import detect_pull_requests, run_baselines
from apps.metrics.rollups import rebuild
from apps.metrics.services import bump_data_version
from apps.metrics.timeframe import day_end_exclusive, day_of, day_start, today
from apps.policy.services import evaluate_pull_requests


class Command(BaseCommand):
    help = (
        "Re-runs derive(), detect(), evaluate() over stored PRs (spec §5.5) then rebuilds "
        "DailyRollup for the same range, without any GitHub call."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--from", dest="date_from", type=datetime.date.fromisoformat, help="ISO date, inclusive."
        )
        parser.add_argument(
            "--to", dest="date_to", type=datetime.date.fromisoformat, help="ISO date, inclusive."
        )
        parser.add_argument(
            "--repo", dest="repos", nargs="+", default=[], help="owner/name, may be repeated."
        )
        parser.add_argument("--project", dest="project", help="Project slug.")
        parser.add_argument(
            "--rollups-only",
            action="store_true",
            help="Skip derive/detect/evaluate; only rebuild rollups.",
        )
        parser.add_argument(
            "--skip-rollups",
            action="store_true",
            help="Skip rollup rebuilding; only run derive/detect/evaluate.",
        )
        parser.add_argument(
            "--baselines",
            action="store_true",
            help="Also recompute the author-baseline structural signals. Off by default because "
            "they are whole-window, not per pull request: the date and repository filters above "
            "do not narrow them, so a filtered recompute would silently recompute everything.",
        )

    @staticmethod
    def _as_date(value: datetime.date | str | None) -> datetime.date | None:
        """`call_command(..., **{"from": "2026-01-01"})` bypasses argparse's `type=` conversion,
        so `value` may still be an ISO string here."""
        if value is None or isinstance(value, datetime.date):
            return value
        return datetime.date.fromisoformat(value)

    def handle(self, *args, **options) -> None:
        if options["rollups_only"] and options["skip_rollups"]:
            raise CommandError("--rollups-only and --skip-rollups are mutually exclusive.")

        queryset = PullRequest.objects.annotate(
            effective_updated_at=Coalesce("updated_at_github", "created_at")
        )
        if options["date_from"] is not None:
            queryset = queryset.filter(effective_updated_at__gte=day_start(options["date_from"]))
        if options["date_to"] is not None:
            queryset = queryset.filter(effective_updated_at__lt=day_end_exclusive(options["date_to"]))
        if options["repos"]:
            queryset = queryset.filter(repository__full_name__in=options["repos"])
        if options["project"]:
            queryset = queryset.filter(repository__projects__slug=options["project"])
        queryset = queryset.distinct()

        if not options["rollups_only"]:
            derived = derive_pull_requests(queryset)
            followup = update_followup_fixes_for(queryset)
            detected = detect_pull_requests(queryset)
            evaluated = evaluate_pull_requests(queryset)
            # Cached metric results are keyed on the data version, and evaluation alone changes
            # what they count (violations, AI status) — so bump it here too, not only after a
            # rollup rebuild, or `--skip-rollups` leaves every dashboard showing the old numbers.
            bump_data_version()
            self.stdout.write(
                f"Recomputed {derived} pull request(s) "
                f"(derive={derived}, followup={followup}, detect={detected}, evaluate={evaluated})."
            )

        if options["baselines"]:
            baseline_result = run_baselines()
            self.stdout.write(
                f"Baseline signals: {baseline_result.authors} author(s), "
                f"{baseline_result.created} created, {baseline_result.deleted} deleted, "
                f"{baseline_result.pull_requests_restatused} pull request(s) restatused."
            )

        if options["skip_rollups"]:
            return

        rollup_from = self._as_date(options["date_from"])
        if rollup_from is None:
            earliest_created_at = (
                PullRequest.objects.order_by("created_at").values_list("created_at", flat=True).first()
            )
            rollup_from = day_of(earliest_created_at) if earliest_created_at is not None else today()
        rollup_to = self._as_date(options["date_to"]) or today()

        result = rebuild(rollup_from, rollup_to)
        bump_data_version()
        self.stdout.write(
            f"Rebuilt rollups for {result.days} day(s), {result.rows} row(s) "
            f"({rollup_from.isoformat()}..{rollup_to.isoformat()})."
        )
