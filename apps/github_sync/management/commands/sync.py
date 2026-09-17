import datetime

from django.core.management.base import BaseCommand

from apps.catalog.models import Repository
from apps.github_sync.models import SyncRun
from apps.github_sync.services import SyncAlreadyRunning, run_sync


class Command(BaseCommand):
    help = "Runs a GitHub sync (spec §5.3): incremental by default, or a full backfill with --full."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--repo",
            action="append",
            dest="repos",
            metavar="OWNER/NAME",
            help="Restrict the sync to this repository. Repeatable.",
        )
        parser.add_argument(
            "--project",
            dest="project",
            metavar="SLUG",
            help="Restrict the sync to this project's repositories.",
        )
        parser.add_argument(
            "--since", dest="since", metavar="YYYY-MM-DD", help="Override the watermark for every repository."
        )
        parser.add_argument(
            "--full",
            action="store_true",
            help="Ignore last_synced_at and start each repository from its sync_since.",
        )

    def handle(self, *args, **options) -> None:
        repos = options["repos"]
        if repos:
            known = set(Repository.objects.filter(full_name__in=repos).values_list("full_name", flat=True))
            unknown = sorted(set(repos) - known)
            if unknown:
                self.stderr.write(f"Unknown repository/repositories: {', '.join(unknown)}")
                raise SystemExit(1)

        since = None
        if options["since"]:
            try:
                since_date = datetime.date.fromisoformat(options["since"])
            except ValueError as exc:
                self.stderr.write(f"--since must be YYYY-MM-DD, got {options['since']!r}.")
                raise SystemExit(1) from exc
            since = datetime.datetime.combine(since_date, datetime.time.min, tzinfo=datetime.UTC)

        try:
            run = run_sync(
                SyncRun.Trigger.CLI,
                repo_full_names=repos,
                project_slug=options["project"],
                since=since,
                full=options["full"],
            )
        except SyncAlreadyRunning as exc:
            self.stderr.write(str(exc))
            raise SystemExit(1) from exc

        self.stdout.write(
            f"Sync finished with status={run.status}: "
            f"{run.stats['repositories']} repositories, {run.stats['pull_requests']} pull requests, "
            f"{run.stats['errors']} errors."
        )
