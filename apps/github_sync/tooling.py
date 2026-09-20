"""The repository AI-tooling probe (phase 12, stage 3): which agent-configuration paths a
repository carries at `HEAD`.

This is a **repository-level** fact and deliberately not an `AISignal`. "The repository has a
`CLAUDE.md`" says nothing about any individual pull request; as a per-PR detector it would fire on
every PR in the repository and drown the signals that actually distinguish one PR from another. A
*change* to one of those paths inside a PR is a different fact, and that stays a `file_path`
detection rule at `confidence: low, disputed: true`.

Cost: one GraphQL request for the repository's root tree, plus at most
`MAX_DIRECTORY_PROBES` more — one for each configured glob whose first segment is a directory that
the root tree actually shows (`.github/copilot-instructions.md` costs a request only in a
repository that has a `.github/`). A repository with no agent tooling therefore costs exactly one
request per sync run.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Sequence

from apps.catalog.globs import compile_globs, matches_any
from apps.catalog.models import Repository
from apps.catalog.services import get_list
from apps.github_sync.client import GitHubClient
from apps.github_sync.errors import GitHubAuthError, GitHubError, GitHubSSOError
from apps.github_sync.queries import REPOSITORY_TREE_QUERY

logger = logging.getLogger(__name__)

# A ceiling on the extra requests one repository may cost, independent of how long a lead makes
# `AI_TOOLING_PATH_GLOBS`. Directories are probed in the configured order, so the entries a lead
# put first are the ones that survive the cut.
MAX_DIRECTORY_PROBES = 5

TREE_ENTRY_TYPE = "tree"


class ToolingProbeUnavailable(GitHubError):
    """The repository has no resolvable tree at `HEAD` — it is empty, or `HEAD` does not resolve.
    Distinct from "probed, found nothing": the caller must not record `[]` for it, because that
    would claim a repository was inspected when it was not."""


def _tree_entries(client: GitHubClient, owner: str, name: str, expression: str) -> list[dict] | None:
    """The entries of one tree, or None when `expression` resolves to nothing or to a blob.

    `object` is nullable by design in GitHub's schema (a path that does not exist answers `null`),
    so a missing node here is data, not a schema drift — `require()` would be wrong.
    """
    data = client.graphql(REPOSITORY_TREE_QUERY, {"owner": owner, "name": name, "expression": expression})
    repository = data.get("repository")
    if not isinstance(repository, dict):
        return None
    tree = repository.get("object")
    if not isinstance(tree, dict):
        return None
    entries = tree.get("entries")
    if entries is None:
        # An `object` with no `entries` key is a blob (the inline fragment did not apply), which
        # for `.github` means a *file* of that name. Nothing to walk into.
        return None
    return [entry for entry in entries if isinstance(entry, dict)]


def _directories_worth_probing(root_entries: Sequence[dict], globs: Sequence[str]) -> list[str]:
    """The first segment of every multi-segment glob, kept only when the root tree shows a
    directory of that name — in the order the globs are configured, deduplicated."""
    directories = {
        entry.get("name")
        for entry in root_entries
        if entry.get("type") == TREE_ENTRY_TYPE and entry.get("name")
    }
    wanted: list[str] = []
    for glob in globs:
        if "/" not in glob:
            continue
        head = glob.split("/", 1)[0]
        if head in directories and head not in wanted:
            wanted.append(head)
    return wanted


def probe_tooling_paths(client: GitHubClient, repository: Repository) -> list[str]:
    """The configured paths this repository carries at `HEAD`, sorted and deduplicated.

    An empty list means "inspected, carries none" — the caller pairs it with
    `ai_tooling_checked_at` so that stays distinguishable from "never inspected".
    """
    globs = [str(glob) for glob in get_list("AI_TOOLING_PATH_GLOBS")]
    if not globs:
        return []
    compiled = compile_globs(globs)
    owner, name = repository.full_name.split("/", 1)

    root_entries = _tree_entries(client, owner, name, "HEAD:")
    if root_entries is None:
        raise ToolingProbeUnavailable(f"{repository.full_name}: no tree at HEAD")

    hits = {
        str(entry["name"])
        for entry in root_entries
        if entry.get("name") and matches_any(str(entry["name"]), compiled)
    }

    for directory in _directories_worth_probing(root_entries, globs)[:MAX_DIRECTORY_PROBES]:
        child_entries = _tree_entries(client, owner, name, f"HEAD:{directory}")
        if child_entries is None:
            continue
        for entry in child_entries:
            child_name = entry.get("name")
            if not child_name:
                continue
            path = f"{directory}/{child_name}"
            if matches_any(path, compiled):
                hits.add(path)

    return sorted(hits)


def record_tooling_paths(client: GitHubClient, repository: Repository, checked_at: datetime.datetime) -> None:
    """Probes and stores, swallowing everything but a credential failure.

    Tooling is a nice-to-have alongside the pull requests a run exists to fetch, so a repository
    whose tree cannot be read keeps its previous value and its previous `checked_at` rather than
    failing the run or recording a false `[]`. `GitHubAuthError`/`GitHubSSOError` still propagate:
    those quarantine the whole connection upstream, and swallowing one here would let the run
    burn a full round of PR requests against a token that is already known to be dead.
    """
    try:
        paths = probe_tooling_paths(client, repository)
    except (GitHubAuthError, GitHubSSOError):
        raise
    except GitHubError as exc:
        logger.info("AI tooling probe skipped for %s: %s", repository.full_name, exc)
        return

    repository.ai_tooling_paths = paths
    repository.ai_tooling_checked_at = checked_at
    repository.save(update_fields=["ai_tooling_paths", "ai_tooling_checked_at"])
