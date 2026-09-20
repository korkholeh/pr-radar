"""The per-PR structural kinds (phase 12, stage 4).

A detector in `detectors.py` matches a regex against text somebody wrote. A *kind* here reads the
shape of the pull request itself: how fast it arrived, how its commits are spaced, how many files
it created, how quickly it answered a review. None of that is proof of anything — which is why
`SignalRule` cannot be `high` confidence at the database level, and why a single structural signal
never moves `ai_status` on its own (see `services.resolve_ai_status`).

Each kind is a pure function of `(params, StructuralContext) -> Iterator[StructuralMatch]`,
mirroring `detectors.DETECTORS` so `services.detect_pull_request` can run both families through
one wanted-vs-existing diff. The two other families live in `baselines.py` (an author's history)
and `diffsignals.py` (the bytes of the change, read from the churn clone).

A kind never touches the database and never reads outcome data: churn, reverts and follow-up fixes
are what this project *measures for* the AI cohort, so using them to decide who is in that cohort
would make the quality dashboards self-proving (PLAN D7, asserted by a test).

Every match carries the thresholds that produced it in its params, not just the measurement. A
lead reading "40 minutes" cannot judge it without knowing the rule said two hours, and a signal
written under an older threshold must be visibly different from one written under the current one
— which is exactly what makes `evidence_hash` retire it when a threshold changes.
"""

from __future__ import annotations

import datetime
import posixpath
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from apps.activity.models import PullRequest
from apps.ai_detection.evidence import EvidenceCode
from apps.ai_detection.models import SignalKind

_SECONDS_PER_HOUR = 3600
_SECONDS_PER_MINUTE = 60


@dataclass(frozen=True)
class StructuralFile:
    path: str
    status: str
    additions: int | None
    deletions: int | None

    @property
    def is_added(self) -> bool:
        return self.status.lower() in {"added", "add", "a"}


@dataclass(frozen=True)
class StructuralCommit:
    committed_at: datetime.datetime | None
    additions: int | None
    deletions: int | None

    @property
    def lines(self) -> int:
        return (self.additions or 0) + (self.deletions or 0)


@dataclass(frozen=True)
class StructuralContext:
    """Everything a kind may read, loaded once per pull request."""

    pull_request: PullRequest
    commits: tuple[StructuralCommit, ...]  # ordered by committed_at, then position
    files: tuple[StructuralFile, ...]  # non-excluded, ordered by path
    review_comment_times: tuple[datetime.datetime, ...]  # ordered, comments by anyone but the author

    @property
    def effective_lines(self) -> int:
        pr = self.pull_request
        additions = pr.effective_additions if pr.effective_additions is not None else pr.additions
        deletions = pr.effective_deletions if pr.effective_deletions is not None else pr.deletions
        return (additions or 0) + (deletions or 0)


@dataclass(frozen=True)
class StructuralMatch:
    code: str
    params: dict[str, Any]


SignalFunction = Callable[[Mapping[str, Any], StructuralContext], Iterator[StructuralMatch]]


def _number(params: Mapping[str, Any], key: str, default: float) -> float:
    value = params.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return value


def _fast_large_pr(params: Mapping[str, Any], ctx: StructuralContext) -> Iterator[StructuralMatch]:
    """A large change whose whole lifetime, first commit to merge, is implausibly short.

    Measured from `first_commit_at`, not `created_at`: opening the PR late says nothing, whereas
    the span from the first line written to the merge is the one a human's typing speed bounds.
    """
    pr = ctx.pull_request
    if pr.first_commit_at is None or pr.merged_at is None:
        return
    min_lines = _number(params, "min_lines", 400)
    min_files = _number(params, "min_files", 8)
    max_hours = _number(params, "max_hours", 2)

    lines = ctx.effective_lines
    files = pr.changed_files or len(ctx.files)
    if lines < min_lines or files < min_files:
        return
    elapsed_hours = (pr.merged_at - pr.first_commit_at).total_seconds() / _SECONDS_PER_HOUR
    if elapsed_hours < 0 or elapsed_hours > max_hours:
        return

    yield StructuralMatch(
        code=EvidenceCode.FAST_LARGE_PR,
        params={
            "lines": lines,
            "files": files,
            "hours": round(elapsed_hours, 1),
            "min_lines": int(min_lines),
            "min_files": int(min_files),
            "max_hours": max_hours,
        },
    )


def _commit_burst(params: Mapping[str, Any], ctx: StructuralContext) -> Iterator[StructuralMatch]:
    """Several substantial commits seconds apart — a human types between commits, a tool does not.

    Only commits above `min_lines_per_commit` count: a run of one-line fixup commits is a normal
    human habit and must not read as a burst.
    """
    min_commits = int(_number(params, "min_commits", 5))
    max_gap_seconds = _number(params, "max_gap_seconds", 90)
    min_lines_per_commit = _number(params, "min_lines_per_commit", 20)

    substantial = [
        commit
        for commit in ctx.commits
        if commit.committed_at is not None and commit.lines >= min_lines_per_commit
    ]
    if len(substantial) < min_commits:
        return

    # The longest run of consecutive substantial commits whose every gap is inside the window,
    # and the widest gap inside that run — the evidence quotes the measurement, not the threshold
    # back at the reader.
    best_length, best_gap = 1, 0.0
    run_length, run_gap = 1, 0.0
    for previous, commit in zip(substantial, substantial[1:], strict=False):
        gap = (commit.committed_at - previous.committed_at).total_seconds()  # type: ignore[operator]
        if 0 <= gap <= max_gap_seconds:
            run_length += 1
            run_gap = max(run_gap, gap)
        else:
            run_length, run_gap = 1, 0.0
        if run_length > best_length:
            best_length, best_gap = run_length, run_gap

    if best_length < min_commits:
        return

    yield StructuralMatch(
        code=EvidenceCode.COMMIT_BURST,
        params={
            "commits": best_length,
            "gap_seconds": int(best_gap),
            "min_commits": min_commits,
            "max_gap_seconds": int(max_gap_seconds),
        },
    )


def _single_large_commit(params: Mapping[str, Any], ctx: StructuralContext) -> Iterator[StructuralMatch]:
    """A large multi-file change that arrived as one commit, with no intermediate state.

    Deliberately conservative: a squash-merged PR also ends up as one commit, so the kind reads
    the PR's *own* commit list rather than the merge commit, and fires only when that list has
    exactly one entry.
    """
    if len(ctx.commits) != 1:
        return
    min_lines = _number(params, "min_lines", 300)
    min_files = _number(params, "min_files", 5)

    lines = ctx.effective_lines
    files = ctx.pull_request.changed_files or len(ctx.files)
    if lines < min_lines or files < min_files:
        return

    yield StructuralMatch(
        code=EvidenceCode.SINGLE_LARGE_COMMIT,
        params={
            "lines": lines,
            "files": files,
            "min_lines": int(min_lines),
            "min_files": int(min_files),
        },
    )


def _mass_file_creation(params: Mapping[str, Any], ctx: StructuralContext) -> Iterator[StructuralMatch]:
    """Many new files spread over many directories in one change — scaffolding, in one sitting."""
    min_added_files = int(_number(params, "min_added_files", 10))
    min_directories = int(_number(params, "min_directories", 3))

    added = [pr_file for pr_file in ctx.files if pr_file.is_added]
    if len(added) < min_added_files:
        return
    directories = {posixpath.dirname(pr_file.path) for pr_file in added}
    if len(directories) < min_directories:
        return

    yield StructuralMatch(
        code=EvidenceCode.MASS_FILE_CREATION,
        params={
            "added_files": len(added),
            "directories": len(directories),
            "min_added_files": min_added_files,
            "min_directories": min_directories,
        },
    )


def _instant_review_response(params: Mapping[str, Any], ctx: StructuralContext) -> Iterator[StructuralMatch]:
    """Repeatedly, a new commit lands moments after a reviewer comments.

    Once is a reviewer catching a typo the author was already fixing. `min_occurrences` times is a
    different pattern. Each comment is counted at most once, and so is each commit, so a single
    commit answering five comments is one occurrence rather than five.
    """
    max_minutes = _number(params, "max_minutes", 5)
    min_occurrences = int(_number(params, "min_occurrences", 3))
    if not ctx.review_comment_times:
        return

    window = max_minutes * _SECONDS_PER_MINUTE
    unused = [commit.committed_at for commit in ctx.commits if commit.committed_at is not None]
    occurrences = 0
    for comment_at in ctx.review_comment_times:
        for index, committed_at in enumerate(unused):
            gap = (committed_at - comment_at).total_seconds()
            if 0 <= gap <= window:
                occurrences += 1
                del unused[index]
                break

    if occurrences < min_occurrences:
        return

    yield StructuralMatch(
        code=EvidenceCode.INSTANT_REVIEW_RESPONSE,
        params={
            "occurrences": occurrences,
            "max_minutes": max_minutes,
            "min_occurrences": min_occurrences,
        },
    )


SIGNAL_FUNCTIONS: dict[str, SignalFunction] = {
    SignalKind.FAST_LARGE_PR: _fast_large_pr,
    SignalKind.COMMIT_BURST: _commit_burst,
    SignalKind.SINGLE_LARGE_COMMIT: _single_large_commit,
    SignalKind.MASS_FILE_CREATION: _mass_file_creation,
    SignalKind.INSTANT_REVIEW_RESPONSE: _instant_review_response,
}


def _commit_sort_key(pr_commit) -> tuple[bool, Any, int]:
    commit = pr_commit.commit
    return (commit.committed_at is None, commit.committed_at, pr_commit.position)


def load_structural_context(pr: PullRequest) -> StructuralContext:
    """Built from the *already prefetched* relations `detectors._prefetch_for_detection` loads, so
    running both signal families over one pull request costs one load, not two."""
    pr_commits = sorted(pr.pull_request_commits.all(), key=_commit_sort_key)
    author_identity_id = pr.author_id
    return StructuralContext(
        pull_request=pr,
        commits=tuple(
            StructuralCommit(
                committed_at=pr_commit.commit.committed_at,
                additions=pr_commit.commit.additions,
                deletions=pr_commit.commit.deletions,
            )
            for pr_commit in pr_commits
        ),
        files=tuple(
            StructuralFile(
                path=pr_file.path,
                status=pr_file.status or "",
                additions=pr_file.additions,
                deletions=pr_file.deletions,
            )
            for pr_file in pr.files.all()
        ),
        # The author commenting on their own pull request is not a review to answer. The identity
        # comparison is only made when both sides are known: an unresolved comment author (or an
        # unresolved PR author) must not silently discard every comment on the pull request, which
        # is what a bare `!=` between two NULLs would do.
        review_comment_times=tuple(
            comment.created_at
            for comment in pr.review_comments.all()
            if comment.created_at is not None
            and not (
                author_identity_id is not None
                and comment.author_id is not None
                and comment.author_id == author_identity_id
            )
        ),
    )


def run_kind(kind: str, params: Mapping[str, Any], ctx: StructuralContext) -> Iterator[StructuralMatch]:
    function = SIGNAL_FUNCTIONS.get(kind)
    if function is None:
        return iter(())
    return function(params, ctx)
