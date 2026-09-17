import datetime

from django.core.management.base import BaseCommand
from django.db.models.functions import Coalesce

from apps.activity.derive import derive_pull_requests
from apps.activity.models import PullRequest
from apps.ai_detection.services import detect_pull_requests
from apps.metrics.timeframe import day_end_exclusive, day_start
from apps.policy.services import evaluate_pull_requests


class Command(BaseCommand):
    help = (
        "Re-runs derive(), detect() then evaluate() over stored PRs (spec §5.5), without any "
        "GitHub call. Phase 7 adds rollup rebuilding to this command."
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
            queryset = queryset.filter(effective_updated_at__gte=day_start(options["date_from"]))
        if options["date_to"] is not None:
            queryset = queryset.filter(effective_updated_at__lt=day_end_exclusive(options["date_to"]))
        if options["repos"]:
            queryset = queryset.filter(repository__full_name__in=options["repos"])
        if options["project"]:
            queryset = queryset.filter(repository__projects__slug=options["project"])
        queryset = queryset.distinct()

        derived = derive_pull_requests(queryset)
        detected = detect_pull_requests(queryset)
        evaluated = evaluate_pull_requests(queryset)
        self.stdout.write(
            f"Recomputed {derived} pull request(s) "
            f"(derive={derived}, detect={detected}, evaluate={evaluated})."
        )
