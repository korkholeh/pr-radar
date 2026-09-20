"""Spec §12: after a sync that fails partway, the real connection token must not be recoverable
from anywhere the tool writes — not the ORM-visible database, not the raw SQLite file on disk, not
the log file, not SyncRun.error_log, and not any rendered page."""

import sqlite3
from pathlib import Path

import pytest
from django.conf import settings
from django.db import connection as db_connection
from django.test import Client
from django.urls import reverse

from apps.catalog.factories import RepositoryFactory
from apps.connections.factories import GitHubConnectionFactory
from apps.connections.services import set_token
from apps.github_sync.models import SyncRun
from apps.github_sync.services import run_sync
from apps.github_sync.tests.conftest import mock_graphql_sequence
from apps.github_sync.tests.test_sync import _tooling_page

TOKEN = "ghp_LeakTestSecretXYZ9876543210"
# Everything but the stored last4 — any occurrence of this fragment anywhere is a leak.
FORBIDDEN = TOKEN[:-4]


def _rate_limit_block() -> dict:
    return {"remaining": 4900, "resetAt": "2026-01-01T01:00:00Z", "cost": 1}


def _pr_list_page() -> dict:
    return {
        "data": {
            "repository": {
                "pullRequests": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "id": "PR_1",
                            "number": 1,
                            "title": "A pull request",
                            "body": "",
                            "state": "OPEN",
                            "isDraft": False,
                            "baseRefName": "main",
                            "headRefName": "feature/1",
                            "createdAt": "2026-01-01T09:00:00Z",
                            "updatedAt": "2026-01-05T09:00:00Z",
                            "mergedAt": None,
                            "closedAt": None,
                            "additions": 1,
                            "deletions": 0,
                            "changedFiles": 1,
                            "mergeCommit": None,
                            "labels": {"nodes": []},
                            "author": {"login": "octocat"},
                            "mergedBy": None,
                        }
                    ],
                }
            },
            "rateLimit": _rate_limit_block(),
        }
    }


def _malformed_commits_page() -> dict:
    """hasNextPage is true but endCursor is missing — the client must raise GitHubSchemaError
    rather than silently stop paginating."""
    return {
        "data": {
            "node": {"commits": {"pageInfo": {"hasNextPage": True}, "nodes": []}},
            "rateLimit": _rate_limit_block(),
        }
    }


def _open_test_db_connection() -> sqlite3.Connection:
    name = db_connection.settings_dict["NAME"]
    if str(name).startswith("file:"):
        return sqlite3.connect(name, uri=True)
    return sqlite3.connect(str(name))


def _assert_no_leak_in_relational_db() -> None:
    with db_connection.cursor() as cursor:
        table_names = db_connection.introspection.table_names(cursor)
        for table in table_names:
            cursor.execute(f'SELECT * FROM "{table}"')  # noqa: S608 -- table names come from introspection
            columns = [col[0] for col in cursor.description]
            for row in cursor.fetchall():
                for column, value in zip(columns, row, strict=True):
                    if isinstance(value, bytes):
                        assert FORBIDDEN.encode() not in value, f"{table}.{column} leaks the token"
                    elif isinstance(value, str):
                        assert FORBIDDEN not in value, f"{table}.{column} leaks the token"


def _assert_no_leak_in_raw_sqlite_file(tmp_path: Path) -> None:
    source = _open_test_db_connection()
    dump_path = tmp_path / "leak-check.sqlite3"
    destination = sqlite3.connect(str(dump_path))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    assert FORBIDDEN.encode() not in dump_path.read_bytes()


def _assert_no_leak_in_log_file() -> None:
    log_path = Path(settings.DATA_DIR) / "logs" / "pr-radar.log"
    if not log_path.exists():
        return
    assert FORBIDDEN not in log_path.read_text(errors="ignore")


@pytest.mark.django_db(transaction=True)
def test_no_token_after_failing_sync(admin_user, tmp_path):
    connection = GitHubConnectionFactory(owner_login="acme")
    set_token(connection, TOKEN)
    RepositoryFactory(connection=connection, full_name="acme/widget")
    # The first response is the repository tooling probe `sync_repository` runs before any
    # pull request (phase 12, stage 3).
    mock_graphql_sequence(_tooling_page(), _pr_list_page(), _malformed_commits_page())

    run = run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    run.refresh_from_db()
    assert run.status == SyncRun.Status.PARTIAL
    assert FORBIDDEN not in run.error_log

    _assert_no_leak_in_relational_db()
    _assert_no_leak_in_raw_sqlite_file(tmp_path)
    _assert_no_leak_in_log_file()

    client = Client()
    client.force_login(admin_user)
    for url in (
        reverse("connections:list"),
        reverse("connections:edit", args=[connection.pk]),
        reverse("github_sync:sync"),
    ):
        response = client.get(url)
        assert FORBIDDEN not in response.content.decode()
