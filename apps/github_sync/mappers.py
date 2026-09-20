"""Pure payload -> model field dict mappers (spec §5.3 step 3). Each mapper raises
GitHubSchemaError, via errors.require(), only for a field the writer depends on; anything the
writer can live without goes through errors.optional() and becomes None, never 0.

Mappers never look up or create rows — that is upserts.py's job, which resolves the *_login
strings this module returns into Identity rows."""

import datetime
import re
from typing import Any

from django.conf import settings

from apps.github_sync.errors import optional, require

_TRAILER_LINE_RE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z-]*): (?P<value>.+)$")
_CO_AUTHOR_RE = re.compile(r"^(?P<name>.+?)\s*<(?P<email>[^>]+)>$")


def _parse_dt(value: str | None) -> datetime.datetime | None:
    if value is None:
        return None
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _raw(node: Any) -> Any:
    return node if settings.STORE_RAW_PAYLOADS else None


def parse_trailers(message: str) -> dict[str, list[str]]:
    """Git trailers are the trailing block of consecutive "Key: value" lines in a commit message."""
    lines = message.rstrip().splitlines()
    trailer_lines: list[str] = []
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            break
        if not _TRAILER_LINE_RE.match(stripped):
            break
        trailer_lines.insert(0, stripped)

    trailers: dict[str, list[str]] = {}
    for line in trailer_lines:
        match = _TRAILER_LINE_RE.match(line)
        assert match is not None
        trailers.setdefault(match.group("key"), []).append(match.group("value"))
    return trailers


def parse_co_authors(trailers: dict[str, list[str]]) -> list[dict[str, str]]:
    co_authors = []
    for value in trailers.get("Co-authored-by", []):
        match = _CO_AUTHOR_RE.match(value.strip())
        if match:
            co_authors.append({"name": match.group("name"), "email": match.group("email")})
    return co_authors


def map_pull_request(node: dict) -> dict:
    message = optional(node, "body", "") or ""
    return {
        "github_id": require(node, "id"),
        "number": require(node, "number"),
        "title": optional(node, "title", "") or "",
        "body": message,
        "state": require(node, "state").lower(),
        "is_draft": optional(node, "isDraft", False),
        "base_ref": optional(node, "baseRefName", "") or "",
        "head_ref": optional(node, "headRefName", "") or "",
        "created_at": _parse_dt(require(node, "createdAt")),
        "updated_at_github": _parse_dt(optional(node, "updatedAt")),
        "merged_at": _parse_dt(optional(node, "mergedAt")),
        "closed_at": _parse_dt(optional(node, "closedAt")),
        "additions": optional(node, "additions"),
        "deletions": optional(node, "deletions"),
        "changed_files": optional(node, "changedFiles"),
        "merge_commit_sha": optional(node, "mergeCommit.oid", "") or "",
        "labels": [label["name"] for label in optional(node, "labels.nodes", []) or []],
        "author_login": optional(node, "author.login"),
        "merged_by_login": optional(node, "mergedBy.login"),
        "raw": _raw(node),
    }


def map_commit(node: dict) -> dict:
    commit = require(node, "commit")
    message = optional(commit, "message", "") or ""
    trailers = parse_trailers(message)
    return {
        "sha": require(commit, "oid"),
        "message": message,
        "authored_at": _parse_dt(optional(commit, "authoredDate")),
        "committed_at": _parse_dt(optional(commit, "committedDate")),
        "additions": optional(commit, "additions"),
        "deletions": optional(commit, "deletions"),
        "trailers": trailers,
        "co_authors": parse_co_authors(trailers),
        "author_login": optional(commit, "author.user.login"),
        "author_email": optional(commit, "author.email"),
        "committer_login": optional(commit, "committer.user.login"),
        "committer_email": optional(commit, "committer.email"),
        "check_rollup_state": optional(commit, "statusCheckRollup.state"),
        "raw": _raw(commit),
    }


def map_review(node: dict) -> dict:
    body = optional(node, "body", "") or ""
    return {
        "github_id": require(node, "id"),
        "reviewer_login": optional(node, "author.login"),
        "state": require(node, "state"),
        "submitted_at": _parse_dt(optional(node, "submittedAt")),
        "body_length": len(body),
        "comments_count": optional(node, "comments.totalCount"),
        "raw": _raw(node),
    }


def map_review_comment(node: dict, *, is_review_thread: bool, is_resolved: bool | None = None) -> dict:
    """`is_resolved` belongs to the *thread*, not the comment, so the caller reads it from the
    thread node and passes it down. `None` means the field was absent — an older GitHub Enterprise
    Server — and stays `None` rather than becoming `False`, because "we do not know" must never be
    stored as "the author ignored this"."""
    body = optional(node, "bodyText", "") or ""
    # `sync_repository` carries the thread's own fields onto this node under `_`-prefixed keys;
    # they are this project's bookkeeping, not GitHub's payload, so they are kept out of `raw`.
    payload = {key: value for key, value in node.items() if not key.startswith("_")}
    return {
        "github_id": require(node, "id"),
        "author_login": optional(node, "author.login"),
        "created_at": _parse_dt(optional(node, "createdAt")),
        "is_review_thread": is_review_thread,
        "is_resolved": is_resolved,
        "body_length": len(body),
        "raw": _raw(payload),
    }


def map_pr_file(node: dict) -> dict:
    return {
        "path": require(node, "path"),
        "status": (optional(node, "changeType", "") or "").lower(),
        "additions": optional(node, "additions"),
        "deletions": optional(node, "deletions"),
    }


def map_check_status(node: dict) -> dict | None:
    """None when the commit carries no status-check rollup at all — nothing to write."""
    commit = require(node, "commit")
    rollup_state = optional(commit, "statusCheckRollup.state")
    if rollup_state is None:
        return None
    return {
        "commit_sha": require(commit, "oid"),
        "rollup_state": rollup_state,
        "observed_at": _parse_dt(optional(commit, "committedDate")),
    }


def _earliest_event_at(nodes: list[dict], typename: str) -> datetime.datetime | None:
    timestamps = [
        _parse_dt(require(node, "createdAt")) for node in nodes if node.get("__typename") == typename
    ]
    return min(timestamps) if timestamps else None


def map_ready_for_review_at(nodes: list[dict]) -> datetime.datetime | None:
    return _earliest_event_at(nodes, "ReadyForReviewEvent")


def map_review_requested_at(nodes: list[dict]) -> datetime.datetime | None:
    """The first time anybody was asked to review, from `REVIEW_REQUESTED_EVENT`. The earliest,
    not the latest: a second reviewer added a week later does not change when the pull request
    started waiting."""
    return _earliest_event_at(nodes, "ReviewRequestedEvent")
