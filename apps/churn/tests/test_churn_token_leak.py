"""Mirrors `tests/test_token_leak.py` for churn (ADR 0004, RISKS row 2): after a real `run_churn`
against a `file://` origin with a real-shaped token on the connection, the token must not appear
in any `git` argv, any remote URL, `.git/config`, a log, or `ChurnResult.error`."""

from __future__ import annotations

import subprocess

import pytest

from apps.catalog.factories import RepositoryFactory
from apps.churn.gitcmd import run_git
from apps.churn.models import ChurnResult
from apps.churn.services import run_churn
from apps.churn.tests.conftest import make_squash_pr
from apps.connections.factories import GitHubConnectionFactory
from apps.connections.services import set_token

TOKEN = "ghp_ChurnLeakSecretXYZ9876543210"
# Everything but the last four chars: any occurrence anywhere is a leak.
FORBIDDEN = TOKEN[:-4]

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _data_dir_in_tmp_path(settings, tmp_path):
    settings.DATA_DIR = tmp_path


def test_token_never_leaks_through_a_full_churn_run(monkeypatch, caplog, git_origin, origin_remote, tmp_path):
    argv_calls: list[list[str]] = []
    real_run = subprocess.run

    def spy(args, **kwargs):
        argv_calls.append(list(args))
        return real_run(args, **kwargs)

    monkeypatch.setattr("apps.churn.gitcmd.subprocess.run", spy)

    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    repository = RepositoryFactory(connection=connection, full_name="acme/leaktest", default_branch="main")
    make_squash_pr(repository, git_origin)

    with caplog.at_level("DEBUG"):
        result = run_churn(window_days=21)

    assert result.computed == 1

    for call_args in argv_calls:
        assert FORBIDDEN not in " ".join(call_args), f"token leaked into argv: {call_args}"

    clone_dir = tmp_path / "repos" / "acme" / "leaktest.git"
    assert FORBIDDEN not in (clone_dir / "config").read_text()

    remote_v = run_git(["-C", str(clone_dir), "remote", "-v"])
    remote_url = run_git(["-C", str(clone_dir), "config", "--get", "remote.origin.url"])
    assert FORBIDDEN not in remote_v
    assert FORBIDDEN not in remote_url

    for row in ChurnResult.objects.all():
        assert FORBIDDEN not in row.error

    assert FORBIDDEN not in caplog.text
