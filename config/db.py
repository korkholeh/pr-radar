"""SQLite connection tuning.

Applies WAL mode and related pragmas on every new connection, and sets Django's
``transaction_mode`` to ``IMMEDIATE`` for SQLite. Both are no-ops for any other
database vendor, so pointing ``DATABASE_URL`` at PostgreSQL changes nothing here.
"""

from django.db.backends.signals import connection_created
from django.dispatch import receiver

SQLITE_PRAGMAS = (
    "PRAGMA journal_mode=WAL;",
    "PRAGMA synchronous=NORMAL;",
    "PRAGMA foreign_keys=ON;",
    "PRAGMA busy_timeout=5000;",
)


@receiver(connection_created)
def apply_sqlite_pragmas(sender, connection, **kwargs) -> None:
    if connection.vendor != "sqlite":
        return
    cursor = connection.cursor()
    try:
        for pragma in SQLITE_PRAGMAS:
            cursor.execute(pragma)
    finally:
        cursor.close()


def configure_sqlite_transaction_mode(database_config: dict) -> dict:
    """Merge transaction_mode=IMMEDIATE into a SQLite DATABASES entry, in place."""
    if database_config.get("ENGINE") == "django.db.backends.sqlite3":
        options = database_config.setdefault("OPTIONS", {})
        options["transaction_mode"] = "IMMEDIATE"
    return database_config
