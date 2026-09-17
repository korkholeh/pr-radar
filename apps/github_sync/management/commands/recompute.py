import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models.functions import Coalesce

from apps.activity.derive import derive_pull_requests
from apps.activity.models import PullRequest
from apps.ai_detection.services import detect_pull_requests


def _as_date(date: datetime.date | str) -> datetime.date:
    """`call_command(..., **{"from": "2026-01-01"})` bypasses argparse's `type=` conversion, so
    `date` may still be an ISO string here."""
    return datetime.date.fromisoformat(date) if isinstance(date, str) else date


def _day_start(date: datetime.date | str) -> datetime.datetime:
    """Midnight of `date` in REPORT_TIMEZONE (spec: day boundaries are Kyiv days, not UTC ones).
    Phase 7's shared rollup day-boundary helper replaces this local conversion."""
    return datetime.datetime.combine(
        _as_date(date), datetime.time.min, tzinfo=ZoneInfo(settings.REPORT_TIMEZONE)
    )


class Command(BaseCommand):
    help = (
        "Re-runs derive() then detect() over stored PRs (spec §5.5), without any GitHub call. "
        "Phase 6 adds policy evaluation and phase 7 adds rollup rebuilding to this command."
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

    def handle(self, *args, **options) -> None:
        queryset = PullRequest.objects.annotate(
            effective_updated_at=Coalesce("updated_at_github", "created_at")
        )
        if options["date_from"] is not None:
            queryset = queryset.filter(effective_updated_at__gte=_day_start(options["date_from"]))
        if options["date_to"] is not None:
            next_day = _as_date(options["date_to"]) + datetime.timedelta(days=1)
            queryset = queryset.filter(effective_updated_at__lt=_day_start(next_day))
        if options["repos"]:
            queryset = queryset.filter(repository__full_name__in=options["repos"])
        if options["project"]:
            queryset = queryset.filter(repository__projects__slug=options["project"])
        queryset = queryset.distinct()

        derived = derive_pull_requests(queryset)
        detected = detect_pull_requests(queryset)
        self.stdout.write(f"Recomputed {derived} pull request(s) (derive={derived}, detect={detected}).")
