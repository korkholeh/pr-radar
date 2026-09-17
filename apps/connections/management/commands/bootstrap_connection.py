import logging
import os

from django.core.management.base import BaseCommand

from apps.catalog.services import get_str
from apps.connections.models import GitHubConnection
from apps.connections.services import set_token, verify_connection

logger = logging.getLogger(__name__)

_ENV_VAR = "GITHUB_TOKEN"


class Command(BaseCommand):
    help = (
        f"Creates a first GitHub connection from the {_ENV_VAR} environment variable, if one is set and "
        "no connection exists yet. Never calls GitHub unless --verify is passed."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--verify",
            action="store_true",
            help="Verify the new connection against GitHub immediately after creating it.",
        )

    def handle(self, *args, **options) -> None:
        token = os.environ.get(_ENV_VAR)

        if GitHubConnection.objects.exists():
            if token:
                logger.warning(
                    "%s is set but a GitHub connection already exists; ignoring the environment variable.",
                    _ENV_VAR,
                )
                self.stdout.write(
                    f"A GitHub connection already exists; ignoring {_ENV_VAR}. Nothing created."
                )
            else:
                self.stdout.write("A GitHub connection already exists. Nothing to do.")
            return

        if not token:
            self.stdout.write(f"{_ENV_VAR} is not set and no GitHub connection exists. Nothing to do.")
            return

        connection = GitHubConnection.objects.create(
            name="Default (.env)",
            kind=get_str("DEFAULT_CONNECTION_KIND"),
        )
        set_token(connection, token)
        self.stdout.write(f"Created connection {connection.name!r} from {_ENV_VAR} (status=unverified).")

        if options["verify"]:
            verify_connection(connection, force=True)
            self.stdout.write(f"Verified {connection.name!r}: status={connection.status}.")
