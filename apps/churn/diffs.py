"""Reading a pull request's diff out of the churn clone (phase 12, stage 6).

The diff signal family needs the bytes of a change, and no table holds them. The one place they
already exist locally is the bare clone `clones.ensure_clone` makes for the churn algorithm — so
diff analysis rides along inside the churn run, reusing that clone, its `CHURN_GIT_TIMEOUT_SECONDS`
timeout and its `CHURN_MAX_FILES` size guard. **The whole stage adds no GitHub API call.**

Division of labour: this module runs `git` and builds a `DiffContext`; `apps/ai_detection/
diffsignals.py` holds the kinds and knows nothing about git. That is what lets the heuristics be
tested on hand-written diffs, and it keeps `gitcmd.run_git()` as the project's only subprocess call
site (ADR 0004).

Everything here runs in a churn worker thread, so — exactly like `churn.services`'s git half — it
reads no `AppSetting`, opens no ORM connection and writes nothing. It also never raises: a
`GitOperationError` becomes an `error` outcome with a masked reason, because one pull request's
broken diff must not take a repository's churn run down with it.

No credential is ever passed to a `git` call here. `clone`/`fetch` are the only operations that
need one, and `ensure_clone` has already done both by the time anything below runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from apps.activity.models import PullRequest
from apps.ai_detection.diffsignals import (
    DiffContext,
    DiffFile,
    DiffRuleSpec,
    DiffTotals,
    collect_diff_facts,
    has_comment_syntax,
    run_diff_rules,
)
from apps.ai_detection.models import DiffAnalysis
from apps.ai_detection.structural import StructuralMatch
from apps.churn.gitcmd import GitOperationError, run_git

logger = logging.getLogger(__name__)

# A change bigger than this is not analysed. `CHURN_MAX_FILES` already bounds the file count, but a
# single generated file can carry a million lines on its own, and the kinds here hold the whole
# diff in memory.
MAX_DIFF_CHARS = 2_000_000
# `git show` calls per pull request for the comment-density baseline. Twenty is well inside a
# repository's time budget and covers the handwritten part of any change worth reading.
MAX_BASE_FILES = 20
# Lines read from one baseline file. A density estimate does not improve past a few thousand lines.
MAX_BASE_LINES = 4_000


@dataclass(frozen=True)
class DiffOutcome:
    """One pull request's diff analysis, computed off the main thread and written back on it.

    `matches` are `(SignalRule pk, match)` pairs for `services.reconcile_diff_signals`; `facts` is
    `DiffFacts.as_dict()` for `DiffAnalysis.facts`. A non-`ok` status carries neither — and the
    caller must then leave the stored signals alone, since a missing clone is not evidence that a
    signal has gone away.
    """

    pull_request_id: int
    status: str
    base_sha: str = ""
    head_sha: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    matches: tuple[tuple[int, StructuralMatch], ...] = ()
    error: str = ""


def _error_outcome(pull_request_id: int, code: str, detail: str) -> DiffOutcome:
    return DiffOutcome(
        pull_request_id=pull_request_id,
        status=DiffAnalysis.Status.ERROR,
        error=f"{code}: {detail}" if detail else code,
    )


def _resolve_revisions(
    pull_request: PullRequest, clone_dir: Path, *, timeout: float | None
) -> tuple[str, str]:
    """`(base, head)` for the change as it landed on the default branch.

    `head` is the merge commit; `base` is its **first** parent, which is the branch tip the change
    went on top of for both merge methods this supports — a squash commit has exactly one parent,
    and a real merge commit's first parent is the target branch. A rebase merge has no single
    commit representing the change at all, which is why `analyse_pull_request_diff` refuses it, the
    same way the churn algorithm does.
    """
    head = pull_request.merge_commit_sha
    if not head:
        raise GitOperationError("no merge commit recorded", reason="no_merge_commit")
    output = run_git(["-C", str(clone_dir), "rev-list", "--parents", "-n", "1", head], timeout=timeout)
    parts = output.split()
    if len(parts) < 2:
        raise GitOperationError("merge commit has no parent", reason="no_parent")
    return parts[1], head


def _numstat(
    clone_dir: Path, base: str, head: str, paths: list[str], *, whitespace_blind: bool, timeout: float | None
) -> tuple[int, int]:
    """`(added, removed)` over `paths`. `whitespace_blind` adds `-w --ignore-blank-lines`, which is
    the whole measurement `wholesale_reformat` rests on: the same diff, counted again with
    formatting-only changes taken out.

    A binary file reports `-` for both counts and contributes nothing, which is correct — a
    heuristic about lines of code has nothing to say about a PNG.
    """
    args = ["-C", str(clone_dir), "diff", "--numstat"]
    if whitespace_blind:
        args += ["-w", "--ignore-blank-lines"]
    args += [base, head, "--", *paths]
    output = run_git(args, timeout=timeout)

    added = removed = 0
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) < 3:
            continue
        if fields[0].isdigit():
            added += int(fields[0])
        if fields[1].isdigit():
            removed += int(fields[1])
    return added, removed


def _parse_unified_diff(diff_text: str) -> dict[str, tuple[list[str], list[str]]]:
    """`{path: (added_lines, removed_lines)}` from a `--unified=0` diff.

    The path comes from the `+++ b/<path>` header rather than from `diff --git`, because that line
    is unambiguous even when a path contains a space. `/dev/null` on that side means the file was
    deleted, and its removed lines are attributed to the `--- a/<path>` name instead.
    """
    per_file: dict[str, tuple[list[str], list[str]]] = {}
    current: tuple[list[str], list[str]] | None = None
    previous_path = ""

    for line in diff_text.splitlines():
        if line.startswith("--- "):
            previous_path = line[4:].removeprefix("a/")
            continue
        if line.startswith("+++ "):
            path = line[4:]
            path = previous_path if path == "/dev/null" else path.removeprefix("b/")
            current = per_file.setdefault(path, ([], []))
            continue
        if current is None or line.startswith(("diff --git", "index ", "@@", "new file", "deleted file")):
            continue
        if line.startswith("+"):
            current[0].append(line[1:])
        elif line.startswith("-"):
            current[1].append(line[1:])
    return per_file


def _base_lines(clone_dir: Path, base: str, path: str, *, timeout: float | None) -> tuple[str, ...]:
    """The file's content before the change, or `()` when it did not exist then (a new file, which
    simply has no local baseline to be an outlier against)."""
    try:
        output = run_git(["-C", str(clone_dir), "show", f"{base}:{path}"], timeout=timeout)
    except GitOperationError:
        return ()
    return tuple(output.splitlines()[:MAX_BASE_LINES])


def load_diff_context(
    pull_request: PullRequest,
    clone_dir: Path,
    *,
    max_files: int,
    git_timeout: float | None = None,
) -> tuple[DiffContext, str, str]:
    """Builds the context the diff kinds read, plus the `(base, head)` it was read at.

    Excluded files (`EXCLUDED_PATH_GLOBS`: lockfiles, vendored trees, generated code) are left out
    of every measurement here for the same reason they are left out of PR size — a heuristic about
    how somebody wrote code must not read a regenerated lockfile as evidence.

    Raises `GitOperationError`; `analyse_pull_request_diff` is the boundary that turns that into an
    outcome.
    """
    base, head = _resolve_revisions(pull_request, clone_dir, timeout=git_timeout)

    pr_files = [f for f in pull_request.files.all() if not f.is_excluded]
    if len(pr_files) > max_files:
        raise GitOperationError(f"{len(pr_files)} files exceeds {max_files}", reason="too_large")
    paths = [f.path for f in pr_files]
    if not paths:
        return DiffContext(pull_request_id=pull_request.id), base, head

    added, removed = _numstat(clone_dir, base, head, paths, whitespace_blind=False, timeout=git_timeout)
    semantic_added, semantic_removed = _numstat(
        clone_dir, base, head, paths, whitespace_blind=True, timeout=git_timeout
    )

    diff_text = run_git(
        ["-C", str(clone_dir), "diff", "--unified=0", "--no-color", base, head, "--", *paths],
        timeout=git_timeout,
    )
    if len(diff_text) > MAX_DIFF_CHARS:
        raise GitOperationError(f"diff is {len(diff_text)} characters", reason="too_large")
    per_file = _parse_unified_diff(diff_text)

    baseline_budget = MAX_BASE_FILES
    files = []
    for pr_file in pr_files:
        added_lines, removed_lines = per_file.get(pr_file.path, ([], []))
        base_content: tuple[str, ...] = ()
        if baseline_budget and not pr_file.is_test and has_comment_syntax(pr_file.path) and added_lines:
            base_content = _base_lines(clone_dir, base, pr_file.path, timeout=git_timeout)
            baseline_budget -= 1
        files.append(
            DiffFile(
                path=pr_file.path,
                added_lines=tuple(added_lines),
                removed_lines=tuple(removed_lines),
                base_lines=base_content,
                is_test=pr_file.is_test,
                is_excluded=False,
            )
        )

    context = DiffContext(
        pull_request_id=pull_request.id,
        files=tuple(files),
        totals=DiffTotals(
            added=added,
            removed=removed,
            semantic_added=semantic_added,
            semantic_removed=semantic_removed,
        ),
    )
    return context, base, head


def analyse_pull_request_diff(
    pull_request: PullRequest,
    clone_dir: Path | None,
    rules: list[DiffRuleSpec],
    *,
    max_files: int,
    git_timeout: float | None = None,
) -> DiffOutcome:
    """The whole analysis for one pull request. Never raises.

    `clone_dir=None` means the repository has no usable clone this run — a connection that cannot
    authenticate, a fetch that failed twice. That is a `no_clone` outcome, not an error: nothing is
    known about the diff, so nothing is written and no stored signal is touched.

    Note the order: facts are collected for every change that could be read, even one that matched
    no rule and even when every diff rule is switched off. `DiffFacts` is what the policy engine
    reads (stage 7), and policy checks are switched on independently of detection rules.
    """
    pr_id = pull_request.id
    if clone_dir is None:
        return DiffOutcome(pull_request_id=pr_id, status=DiffAnalysis.Status.NO_CLONE)
    if pull_request.merge_method == PullRequest.MergeMethod.REBASE:
        return DiffOutcome(pull_request_id=pr_id, status=DiffAnalysis.Status.UNSUPPORTED_MERGE_METHOD)

    try:
        context, base, head = load_diff_context(
            pull_request, clone_dir, max_files=max_files, git_timeout=git_timeout
        )
    except GitOperationError as exc:
        if exc.reason == "too_large":
            return DiffOutcome(pull_request_id=pr_id, status=DiffAnalysis.Status.TOO_LARGE, error=str(exc))
        return _error_outcome(pr_id, exc.reason, str(exc))
    except Exception:  # pragma: no cover - belt and suspenders, like churn's own worker boundary
        logger.exception("Diff analysis: unexpected failure on pull request %s.", pr_id)
        return _error_outcome(pr_id, "analysis_failed", "unexpected failure")

    return DiffOutcome(
        pull_request_id=pr_id,
        status=DiffAnalysis.Status.OK,
        base_sha=base,
        head_sha=head,
        facts=collect_diff_facts(context).as_dict(),
        matches=tuple(run_diff_rules(rules, context)),
    )
