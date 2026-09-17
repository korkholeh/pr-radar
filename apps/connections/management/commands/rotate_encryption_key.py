from django.core.management.base import BaseCommand
from django.db import transaction

from apps.connections.crypto import encrypt_token, is_encrypted_with_primary_key
from apps.connections.models import GitHubConnection
from apps.connections.services import plaintext_token


class Command(BaseCommand):
    help = (
        "Re-encrypts every stored token under the first key in FIELD_ENCRYPTION_KEYS. "
        "Run after adding a new key at position 0 and before removing an old key."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run", action="store_true", help="Report how many rows would move without writing."
        )

    def handle(self, *args, **options) -> None:
        from django.conf import settings

        if not settings.FIELD_ENCRYPTION_KEYS:
            self.stderr.write("FIELD_ENCRYPTION_KEYS is empty; refusing to run.")
            raise SystemExit(1)

        dry_run = options["dry_run"]
        connections = list(GitHubConnection.objects.exclude(token_encrypted=None))
        moved = 0

        with transaction.atomic():
            for connection in connections:
                if is_encrypted_with_primary_key(bytes(connection.token_encrypted)):
                    continue
                moved += 1
                if not dry_run:
                    connection.token_encrypted = encrypt_token(plaintext_token(connection))
                    connection.save(update_fields=["token_encrypted"])
            if dry_run:
                transaction.set_rollback(True)

        # private_key_encrypted is always null in v1 (GitHub App auth is reserved, not implemented).

        if dry_run:
            self.stdout.write(f"Would re-encrypt {moved} of {len(connections)} connection token(s).")
        else:
            self.stdout.write(f"Re-encrypted {moved} of {len(connections)} connection token(s).")
