"""The repository AI-tooling probe (phase 12, stage 3). Everything here runs against the JSON
fixtures in tests/fixtures/github/ through respx; `conftest.py` fails the suite on any unmocked
outbound request, so no test can reach GitHub."""

import json
from pathlib import Path

import pytest
from django.utils import timezone

from apps.ai_detection.models import AISignal
from apps.catalog.factories import RepositoryFactory
from apps.catalog.models import Repository
from apps.catalog.services import set_setting
from apps.connections.factories import GitHubConnectionFactory
from apps.connections.services import set_token
from apps.github_sync.models import SyncRun
from apps.github_sync.services import run_sync
from apps.github_sync.tests.conftest import mock_graphql_sequence
from apps.github_sync.tests.test_sync import _one_pr_sequence, _tooling_page
from apps.github_sync.tooling import (
    MAX_DIRECTORY_PROBES,
    ToolingProbeUnavailable,
    probe_tooling_paths,
)

_FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "github"
TOKEN = "ghp_secrettokenvalue0123456789"


def _fixture(name):
    return json.loads((_FIXTURES_DIR / f"{name}.json").read_text())


def _make_repository(**kwargs):
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    return RepositoryFactory(connection=connection, **kwargs)


def _sequence(*tooling_pages):
    """`tooling_pages` followed by the pull-request pages of `_one_pr_sequence`, whose own
    leading tooling page is dropped so the caller controls what the probe sees."""
    return list(tooling_pages) + _one_pr_sequence()[1:]


@pytest.mark.django_db
def test_sync_records_the_tooling_paths_the_repository_carries():
    repository = _make_repository(full_name="acme/widget")
    mock_graphql_sequence(
        *_sequence(_fixture("repository_tree_head"), _fixture("repository_tree_dot_github"))
    )

    run = run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    repository.refresh_from_db()
    assert repository.ai_tooling_paths == [
        ".agents",
        ".github/copilot-instructions.md",
        "AGENTS.md",
    ]
    assert repository.ai_tooling_checked_at == run.started_at


@pytest.mark.django_db
def test_a_repository_with_no_agent_configuration_records_an_empty_list_not_null():
    """`[]` plus a timestamp is "probed, carries none". `None` would be "never probed", and the
    two must stay distinguishable — the repository page says different things about them."""
    repository = _make_repository(full_name="acme/widget")
    mock_graphql_sequence(*_one_pr_sequence())  # the default fixture is a repository with none

    run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    repository.refresh_from_db()
    assert repository.ai_tooling_paths == []
    assert repository.ai_tooling_checked_at is not None


@pytest.mark.django_db
def test_a_repository_whose_head_does_not_resolve_keeps_its_previous_answer():
    """An empty repository, or one whose default branch has moved, must not be recorded as
    "carries no agent configuration" — that is an answer nobody obtained."""
    repository = _make_repository(full_name="acme/widget")
    checked_before = timezone.now() - timezone.timedelta(days=3)
    Repository.objects.filter(pk=repository.pk).update(
        ai_tooling_paths=["CLAUDE.md"], ai_tooling_checked_at=checked_before
    )
    mock_graphql_sequence(*_sequence(_fixture("repository_tree_unresolvable")))

    run = run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    run.refresh_from_db()
    assert run.status == SyncRun.Status.SUCCESS  # a failed probe never fails the run
    repository.refresh_from_db()
    assert repository.ai_tooling_paths == ["CLAUDE.md"]
    assert repository.ai_tooling_checked_at == checked_before


@pytest.mark.django_db
def test_the_probe_creates_no_ai_signal():
    """The whole point of keeping this off `AISignal`: "the repository has a CLAUDE.md" is true
    of every pull request in it, so as a signal it would drown the ones that are not."""
    _make_repository(full_name="acme/widget")
    mock_graphql_sequence(
        *_sequence(_fixture("repository_tree_head"), _fixture("repository_tree_dot_github"))
    )

    run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    assert AISignal.objects.count() == 0


@pytest.mark.django_db
def test_a_directory_no_configured_glob_names_is_never_probed():
    """The cost promise: one request for a repository with no agent tooling. `src/` is a
    directory, but no glob starts with `src/`, so nothing walks into it — which is why the
    sequence below carries no page for it and the sync still succeeds."""
    repository = _make_repository(full_name="acme/widget")
    mock_graphql_sequence(*_one_pr_sequence())

    run = run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    run.refresh_from_db()
    assert run.status == SyncRun.Status.SUCCESS
    repository.refresh_from_db()
    assert repository.ai_tooling_paths == []


@pytest.mark.django_db
def test_an_empty_glob_list_records_nothing_and_asks_github_nothing():
    set_setting("AI_TOOLING_PATH_GLOBS", [])
    repository = _make_repository(full_name="acme/widget")
    # No tooling page at all: with no globs configured the probe must make no request, so the
    # first response has to be consumed by the pull-request query.
    mock_graphql_sequence(*_one_pr_sequence()[1:])

    run = run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])

    run.refresh_from_db()
    assert run.status == SyncRun.Status.SUCCESS
    repository.refresh_from_db()
    assert repository.ai_tooling_paths == []


@pytest.mark.django_db
def test_re_syncing_refreshes_the_stored_paths():
    repository = _make_repository(full_name="acme/widget")
    mock_graphql_sequence(
        *_sequence(_fixture("repository_tree_head"), _fixture("repository_tree_dot_github")),
        *_sequence(_fixture("repository_tree_no_tooling")),
    )

    run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"])
    repository.refresh_from_db()
    assert repository.ai_tooling_paths != []

    run_sync(SyncRun.Trigger.CLI, repo_full_names=["acme/widget"], full=True)

    repository.refresh_from_db()
    assert repository.ai_tooling_paths == []


@pytest.mark.django_db
def test_the_shared_tooling_page_helper_matches_the_committed_fixture():
    """`_tooling_page()` is prepended to every ordered response sequence in the sync tests; if
    the fixture it reads ever grows an agent directory, those sequences would need a second
    page and would start failing in confusing ways."""
    entries = _tooling_page()["data"]["repository"]["object"]["entries"]
    names = {entry["name"] for entry in entries}
    assert not names & {".agents", ".claude", ".github", "AGENTS.md", "CLAUDE.md"}


# -- the matching and probe-selection logic, without a sync run ----------------------------------


class _StubClient:
    """Answers `REPOSITORY_TREE_QUERY` from a dict of expression -> entries. Nothing here touches
    the network; the respx guard in conftest.py would fail the test if it did."""

    def __init__(self, trees):
        self.trees = trees
        self.expressions = []

    def graphql(self, document, variables):
        expression = variables["expression"]
        self.expressions.append(expression)
        entries = self.trees.get(expression)
        obj = None if entries is None else {"oid": "a" * 40, "entries": entries}
        return {"repository": {"object": obj}}


def _entry(name, kind="blob"):
    return {"name": name, "type": kind}


@pytest.mark.django_db
def test_only_directories_a_configured_glob_names_are_walked_into():
    set_setting("AI_TOOLING_PATH_GLOBS", [".claude/skills", ".github/copilot-instructions.md"])
    repository = RepositoryFactory(full_name="acme/widget")
    client = _StubClient(
        {
            "HEAD:": [_entry(".github", "tree"), _entry("src", "tree"), _entry("README.md")],
            "HEAD:.github": [_entry("copilot-instructions.md"), _entry("workflows", "tree")],
        }
    )

    paths = probe_tooling_paths(client, repository)

    assert paths == [".github/copilot-instructions.md"]
    # `src` is a directory but no glob names it, and `.claude` is named but absent — neither costs
    # a request.
    assert client.expressions == ["HEAD:", "HEAD:.github"]


@pytest.mark.django_db
def test_a_glob_naming_a_directory_matches_that_directory_itself():
    set_setting("AI_TOOLING_PATH_GLOBS", [".claude", "CLAUDE.md"])
    repository = RepositoryFactory(full_name="acme/widget")
    client = _StubClient({"HEAD:": [_entry(".claude", "tree"), _entry("CLAUDE.md")]})

    assert probe_tooling_paths(client, repository) == [".claude", "CLAUDE.md"]
    assert client.expressions == ["HEAD:"]  # no multi-segment glob, so nothing is walked into


@pytest.mark.django_db
def test_a_directory_probe_that_resolves_to_nothing_is_skipped_not_fatal():
    set_setting("AI_TOOLING_PATH_GLOBS", [".github/copilot-instructions.md"])
    repository = RepositoryFactory(full_name="acme/widget")
    client = _StubClient({"HEAD:": [_entry(".github", "tree")]})  # no HEAD:.github entry

    assert probe_tooling_paths(client, repository) == []


@pytest.mark.django_db
def test_an_unresolvable_head_is_not_an_empty_result():
    set_setting("AI_TOOLING_PATH_GLOBS", [".claude"])
    repository = RepositoryFactory(full_name="acme/widget")

    with pytest.raises(ToolingProbeUnavailable):
        probe_tooling_paths(_StubClient({}), repository)


@pytest.mark.django_db
def test_the_number_of_directory_probes_is_capped():
    globs = [f".dir{index}/config.md" for index in range(MAX_DIRECTORY_PROBES + 3)]
    set_setting("AI_TOOLING_PATH_GLOBS", globs)
    repository = RepositoryFactory(full_name="acme/widget")
    trees = {"HEAD:": [_entry(f".dir{index}", "tree") for index in range(len(globs))]}
    trees.update({f"HEAD:.dir{index}": [_entry("config.md")] for index in range(len(globs))})
    client = _StubClient(trees)

    paths = probe_tooling_paths(client, repository)

    assert len(client.expressions) == MAX_DIRECTORY_PROBES + 1  # the root, then the capped probes
    assert paths == [f".dir{index}/config.md" for index in range(MAX_DIRECTORY_PROBES)]
