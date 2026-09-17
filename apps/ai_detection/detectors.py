"""The eight AI-detection heuristics (spec §6.1). Each detector is a pure function of a compiled
rule pattern and a `DetectionContext` loaded once per PR; none of them writes to the database or
knows about `DetectionRule` rows — that wiring lives in `services.py`."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from django.db.models import QuerySet

from apps.activity.models import Commit, PullRequest
from apps.ai_detection.models import Detector as DetectorCode

EVIDENCE_MAX_LENGTH = 200

_HTML_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)
_ELLIPSIS = "…"


@dataclass(frozen=True)
class DetectionContext:
    """Everything a detector may read, loaded once per PR."""

    pull_request: PullRequest
    commits: tuple[Commit, ...]  # ordered committed_at, then sha
    author_values: tuple[str, ...]  # the PR author identity's value(s)
    labels: tuple[str, ...]
    head_ref: str
    body: str


@dataclass(frozen=True)
class DetectorMatch:
    evidence: str  # already truncated to <= EVIDENCE_MAX_LENGTH
    commit_id: int | None


Detector = Callable[[re.Pattern[str], DetectionContext], Iterator[DetectorMatch]]


def evidence_fragment(haystack: str, match: re.Match[str]) -> str:
    """The <=200-char slice of `haystack` around `match`, ellipsised at either cut end."""
    if len(haystack) <= EVIDENCE_MAX_LENGTH:
        return haystack

    start, end = match.span()
    center = (start + end) // 2
    half = EVIDENCE_MAX_LENGTH // 2
    window_start = max(0, center - half)
    window_end = min(len(haystack), window_start + EVIDENCE_MAX_LENGTH)
    window_start = max(0, window_end - EVIDENCE_MAX_LENGTH)

    fragment = haystack[window_start:window_end]
    if window_start > 0:
        fragment = _ELLIPSIS + fragment[1:]
    if window_end < len(haystack):
        fragment = fragment[:-1] + _ELLIPSIS
    return fragment


def _render_trailers(commit: Commit) -> str:
    return "\n".join(f"{key}: {value}" for key, values in commit.trailers.items() for value in values)


def _commit_author_haystack(commit: Commit) -> str:
    values = [
        identity.value
        for identity in (commit.author_identity, commit.author_email_identity, commit.committer_identity)
        if identity is not None
    ]
    for co_author in commit.co_authors:
        if co_author.get("name"):
            values.append(co_author["name"])
        if co_author.get("email"):
            values.append(co_author["email"])
    return "\n".join(values)


def _strip_html_comments(body: str) -> str:
    return _HTML_COMMENT_RE.sub("", body)


def _html_comment_contents(body: str) -> str:
    return "\n".join(_HTML_COMMENT_RE.findall(body))


def _detect_commit_trailer(pattern: re.Pattern[str], ctx: DetectionContext) -> Iterator[DetectorMatch]:
    for commit in ctx.commits:
        haystack = _render_trailers(commit)
        match = pattern.search(haystack)
        if match is not None:
            yield DetectorMatch(evidence=evidence_fragment(haystack, match), commit_id=commit.pk)


def _detect_commit_author(pattern: re.Pattern[str], ctx: DetectionContext) -> Iterator[DetectorMatch]:
    for commit in ctx.commits:
        haystack = _commit_author_haystack(commit)
        match = pattern.search(haystack)
        if match is not None:
            yield DetectorMatch(evidence=evidence_fragment(haystack, match), commit_id=commit.pk)


def _detect_pr_author(pattern: re.Pattern[str], ctx: DetectionContext) -> Iterator[DetectorMatch]:
    haystack = "\n".join(ctx.author_values)
    match = pattern.search(haystack)
    if match is not None:
        yield DetectorMatch(evidence=evidence_fragment(haystack, match), commit_id=None)


def _detect_pr_body_footer(pattern: re.Pattern[str], ctx: DetectionContext) -> Iterator[DetectorMatch]:
    haystack = _strip_html_comments(ctx.body)
    match = pattern.search(haystack)
    if match is not None:
        yield DetectorMatch(evidence=evidence_fragment(haystack, match), commit_id=None)


def _detect_html_comment(pattern: re.Pattern[str], ctx: DetectionContext) -> Iterator[DetectorMatch]:
    haystack = _html_comment_contents(ctx.body)
    match = pattern.search(haystack)
    if match is not None:
        yield DetectorMatch(evidence=evidence_fragment(haystack, match), commit_id=None)


def _detect_label(pattern: re.Pattern[str], ctx: DetectionContext) -> Iterator[DetectorMatch]:
    for label in ctx.labels:
        match = pattern.search(label)
        if match is not None:
            yield DetectorMatch(evidence=evidence_fragment(label, match), commit_id=None)


def _detect_branch_pattern(pattern: re.Pattern[str], ctx: DetectionContext) -> Iterator[DetectorMatch]:
    match = pattern.search(ctx.head_ref)
    if match is not None:
        yield DetectorMatch(evidence=evidence_fragment(ctx.head_ref, match), commit_id=None)


def _detect_commit_message(pattern: re.Pattern[str], ctx: DetectionContext) -> Iterator[DetectorMatch]:
    for commit in ctx.commits:
        haystack = commit.message
        match = pattern.search(haystack)
        if match is not None:
            yield DetectorMatch(evidence=evidence_fragment(haystack, match), commit_id=commit.pk)


DETECTORS: dict[str, Detector] = {
    DetectorCode.COMMIT_TRAILER: _detect_commit_trailer,
    DetectorCode.COMMIT_AUTHOR: _detect_commit_author,
    DetectorCode.PR_AUTHOR: _detect_pr_author,
    DetectorCode.PR_BODY_FOOTER: _detect_pr_body_footer,
    DetectorCode.HTML_COMMENT: _detect_html_comment,
    DetectorCode.LABEL: _detect_label,
    DetectorCode.BRANCH_PATTERN: _detect_branch_pattern,
    DetectorCode.COMMIT_MESSAGE: _detect_commit_message,
}


def _commit_sort_key(commit: Commit) -> tuple[bool, object, str]:
    return (commit.committed_at is None, commit.committed_at, commit.sha)


def _prefetch_for_detection(queryset: QuerySet[PullRequest]) -> QuerySet[PullRequest]:
    return queryset.select_related("author").prefetch_related(
        "pull_request_commits__commit__author_identity",
        "pull_request_commits__commit__author_email_identity",
        "pull_request_commits__commit__committer_identity",
    )


def _context_from_pull_request(pr: PullRequest) -> DetectionContext:
    commits = tuple(sorted((prc.commit for prc in pr.pull_request_commits.all()), key=_commit_sort_key))
    author_values = (pr.author.value,) if pr.author is not None else ()
    return DetectionContext(
        pull_request=pr,
        commits=commits,
        author_values=author_values,
        labels=tuple(pr.labels),
        head_ref=pr.head_ref,
        body=pr.body or "",
    )


def load_context(pull_request_id: int) -> DetectionContext:
    pr = _prefetch_for_detection(PullRequest.objects.all()).get(pk=pull_request_id)
    return _context_from_pull_request(pr)


def load_contexts(queryset: QuerySet[PullRequest]) -> Iterator[DetectionContext]:
    """Batch form of `load_context`: one query plus prefetches for the whole page of PRs, so a
    caller that needs every PR's context (the dry run) does not pay `load_context`'s query cost
    once per PR."""
    for pr in _prefetch_for_detection(queryset):
        yield _context_from_pull_request(pr)
