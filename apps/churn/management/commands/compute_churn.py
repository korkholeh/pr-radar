from django.core.management.base import BaseCommand, CommandError

from apps.catalog.models import Repository
from apps.churn.services import run_churn


class Command(BaseCommand):
    help = "Computes churn (spec §9) for eligible merged PRs and writes ChurnResult rows."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--repo", dest="repos", nargs="+", default=[], help="owner/name, may be repeated."
        )
        parser.add_argument("--project", dest="project", help="Project slug.")
        parser.add_argument(
            "--window", dest="window", type=int, help="Window in days; defaults to CHURN_WINDOW_DAYS."
        )
        parser.add_argument("--limit", dest="limit", type=int, help="Maximum number of PRs to compute.")

    def handle(self, *args, **options) -> None:
        repos = options["repos"] or None
        if repos:
            existing = set(Repository.objects.filter(full_name__in=repos).values_list("full_name", flat=True))
            unknown = sorted(set(repos) - existing)
            if unknown:
                raise CommandError(f"Unknown repositories: {', '.join(unknown)}")

        result = run_churn(
            window_days=options["window"],
            repo_full_names=repos,
            project_slug=options["project"],
            limit=options["limit"],
        )
        self.stdout.write(
            f"computed={result.computed} skipped={result.skipped} too_large={result.too_large} "
            f"unsupported={result.unsupported} errors={result.errors}"
        )
