"""The twelve AI-detection heuristics (spec §6.1; four of them added in phase 12). Each detector
is a pure function of a compiled rule pattern and a `DetectionContext` loaded once per PR; none of
them writes to the database or knows about `DetectionRule` rows — that wiring lives in
`services.py`."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from django.db.models import Prefetch, QuerySet

from apps.activity.models import Commit, PRFile, PullRequest, Review, ReviewComment
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
    title: str = ""
    file_paths: tuple[str, ...] = ()  # non-excluded, ordered by path
    reviewer_values: tuple[str, ...] = ()  # ordered by submitted_at, then github_id
    merged_by_values: tuple[str, ...] = ()


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


def _detect_file_path(pattern: re.Pattern[str], ctx: DetectionContext) -> Iterator[DetectorMatch]:
    """At most one match per rule, the first path in ascending order: a PR that touches 400 files
    must not produce 400 signals that all say the same thing."""
    for path in ctx.file_paths:
        match = pattern.search(path)
        if match is not None:
            yield DetectorMatch(evidence=evidence_fragment(path, match), commit_id=None)
            return


def _detect_pr_title(pattern: re.Pattern[str], ctx: DetectionContext) -> Iterator[DetectorMatch]:
    match = pattern.search(ctx.title)
    if match is not None:
        yield DetectorMatch(evidence=evidence_fragment(ctx.title, match), commit_id=None)


def _detect_reviewer_identity(pattern: re.Pattern[str], ctx: DetectionContext) -> Iterator[DetectorMatch]:
    """Bounded the same way as `file_path`: one match per rule, the earliest review that carries
    it. Detects an AI *reviewer*, which is why every seeded rule for it is disputed."""
    for value in ctx.reviewer_values:
        match = pattern.search(value)
        if match is not None:
            yield DetectorMatch(evidence=evidence_fragment(value, match), commit_id=None)
            return


def _detect_merged_by_identity(pattern: re.Pattern[str], ctx: DetectionContext) -> Iterator[DetectorMatch]:
    haystack = "\n".join(ctx.merged_by_values)
    match = pattern.search(haystack)
    if match is not None:
        yield DetectorMatch(evidence=evidence_fragment(haystack, match), commit_id=None)


DETECTORS: dict[str, Detector] = {
    DetectorCode.COMMIT_TRAILER: _detect_commit_trailer,
    DetectorCode.COMMIT_AUTHOR: _detect_commit_author,
    DetectorCode.PR_AUTHOR: _detect_pr_author,
    DetectorCode.PR_BODY_FOOTER: _detect_pr_body_footer,
    DetectorCode.HTML_COMMENT: _detect_html_comment,
    DetectorCode.LABEL: _detect_label,
    DetectorCode.BRANCH_PATTERN: _detect_branch_pattern,
    DetectorCode.COMMIT_MESSAGE: _detect_commit_message,
    DetectorCode.FILE_PATH: _detect_file_path,
    DetectorCode.PR_TITLE: _detect_pr_title,
    DetectorCode.REVIEWER_IDENTITY: _detect_reviewer_identity,
    DetectorCode.MERGED_BY_IDENTITY: _detect_merged_by_identity,
}


def _commit_sort_key(commit: Commit) -> tuple[bool, object, str]:
    return (commit.committed_at is None, commit.committed_at, commit.sha)


def _prefetch_for_detection(queryset: QuerySet[PullRequest]) -> QuerySet[PullRequest]:
    """One query per relation, never one per row: the `file_path` and `reviewer_identity`
    detectors read whole collections, so the budget must stay flat in files and reviews.

    The two `Prefetch`es also fix the order the detectors iterate in — a rule that matches two
    paths must always report the same one — and drop excluded files before they reach a rule."""
    return queryset.select_related("author", "merged_by").prefetch_related(
        "pull_request_commits__commit__author_identity",
        "pull_request_commits__commit__author_email_identity",
        "pull_request_commits__commit__committer_identity",
        Prefetch("files", queryset=PRFile.objects.filter(is_excluded=False).order_by("path")),
        Prefetch(
            "reviews",
            queryset=Review.objects.select_related("reviewer").order_by("submitted_at", "github_id"),
        ),
        # Not read by any detector: the structural kinds in `structural.py` build their context
        # from this same loaded pull request (`services.detect_pull_request` runs both families
        # over one load), and `instant_review_response` needs the comment timestamps.
        Prefetch("review_comments", queryset=ReviewComment.objects.order_by("created_at")),
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
        title=pr.title or "",
        file_paths=tuple(pr_file.path for pr_file in pr.files.all()),
        reviewer_values=tuple(
            review.reviewer.value for review in pr.reviews.all() if review.reviewer is not None
        ),
        merged_by_values=(pr.merged_by.value,) if pr.merged_by is not None else (),
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
