from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.catalog.factories import RepositoryFactory
from apps.churn.models import ChurnResult
from apps.churn.tests.conftest import make_squash_pr
from apps.connections.factories import GitHubConnectionFactory
from apps.connections.services import set_token

pytestmark = pytest.mark.django_db

TOKEN = "ghp_secrettokenvalue0123456789"


@pytest.fixture(autouse=True)
def _data_dir_in_tmp_path(settings, tmp_path):
    settings.DATA_DIR = tmp_path


@pytest.fixture
def repository(git_origin, origin_remote):
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    return RepositoryFactory(connection=connection, default_branch="main")


def test_call_command_writes_the_same_rows_as_run_churn_and_prints_a_summary(repository, git_origin):
    pr = make_squash_pr(repository, git_origin)
    out = StringIO()

    call_command("compute_churn", window=21, stdout=out)

    assert ChurnResult.objects.filter(pull_request=pr, window_days=21).exists()
    assert "computed=1" in out.getvalue()


def test_an_unknown_repo_raises_a_command_error():
    with pytest.raises(CommandError, match="Unknown repositories"):
        call_command("compute_churn", repo=["no/such-repo"])
