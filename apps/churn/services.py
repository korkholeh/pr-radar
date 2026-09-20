"""The §9 churn algorithm and the run orchestration (RISKS rows 11, 14).

Git work (`compute_churn_for_pull_request`) runs off the main thread in `run_churn`'s
`ThreadPoolExecutor`; every `ChurnResult` write happens back on the main thread, which is what
keeps this component -- the most parallel one in the system -- out of SQLite write contention.
AppSettings (`CHURN_MAX_FILES`, `CHURN_GIT_TIMEOUT_SECONDS`, `CHURN_REPO_TIME_BUDGET_SECONDS`,
`CHURN_MAX_WORKERS`) are read exactly once, on the main thread, before any worker starts -- a
worker thread must never open its own ORM connection (RISKS row 14: a cold settings cache falls
through to a query, and concurrent workers racing that query is the exact contention this
component exists to avoid).

Phase 12, stage 6 added a second passenger to the same run: diff analysis
(`apps/churn/diffs.py`), which needs the clone this component already makes and therefore costs no
GitHub API call. It is opt-in per repository (`DIFF_ANALYSIS_REPOSITORIES`), it takes what is left
of a repository's time budget after churn, and it writes through the same main-thread boundary."""

from __future__ import annotations

import datetime
import logging
import time
from collections import defaultdict
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from django.db.models import Prefetch

from apps.activity.models import PRFile, PullRequest
from apps.ai_detection.diffsignals import DiffRuleSpec
from apps.ai_detection.models import DiffAnalysis
from apps.ai_detection.services import diff_rule_specs, reconcile_diff_signals
from apps.catalog.models import Repository
from apps.catalog.services import get_int, get_list
from apps.churn.blame import blame_counts, path_exists_at, resolve_path_at
from apps.churn.clones import ensure_clone
from apps.churn.diffs import DiffOutcome, analyse_pull_request_diff
from apps.churn.gitcmd import GitOperationError, run_git
from apps.churn.models import ChurnResult
from apps.churn.selectors import eligible_pull_requests, pull_requests_needing_diff_analysis
from apps.connections.auth import ConnectionNotUsableError, auth_for_connection
from apps.metrics.services import bump_data_version

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChurnOutcome:
    pull_request_id: int
    window_days: int
    status: str
    lines_at_merge: int | None = None
    lines_surviving: int | None = None
    churn_ratio: float | None = None
    snapshot_sha: str = ""
    error: str = ""
    skip: bool = False


@dataclass(frozen=True)
class ChurnRunResult:
    computed: int = 0
    skipped: int = 0
    too_large: int = 0
    unsupported: int = 0
    errors: int = 0
    # Diff analysis (phase 12, stage 6) rides along in this run because it needs the same clone.
    diffs_analysed: int = 0
    diffs_unreadable: int = 0
    diff_signals_created: int = 0
    diff_signals_deleted: int = 0


@dataclass(frozen=True)
class RepositoryWork:
    """What one worker thread produced for one repository: churn outcomes, and — when the
    repository is opted in to diff analysis — diff outcomes. Both are written back on the main
    thread, which is what keeps this component out of SQLite write contention."""

    churn: list[ChurnOutcome] = field(default_factory=list)
    diffs: list[DiffOutcome] = field(default_factory=list)


def _error_outcome(pull_request_id: int, window_days: int, code: str, detail: str) -> ChurnOutcome:
    message = f"{code}: {detail}" if detail else code
    return ChurnOutcome(pull_request_id, window_days, ChurnResult.Status.ERROR, error=message)


def _pr_commit_shas(pull_request: PullRequest) -> set[str]:
    """The PR's own commit shas, from already-loaded (or lazily queried) relations -- never a
    fresh `.filter()`/`.values_list()` call, so a prefetched instance needs no further query
    (RISKS row 14: worker threads must never open their own ORM connection)."""
    return {pc.commit.sha for pc in pull_request.pull_request_commits.all()}


def _pr_commit_set(pull_request: PullRequest, clone_dir: Path, *, timeout: float | None) -> set[str]:
    if pull_request.merge_method == PullRequest.MergeMethod.SQUASH:
        return {pull_request.merge_commit_sha} if pull_request.merge_commit_sha else set()

    if pull_request.merge_method == PullRequest.MergeMethod.MERGE:
        return _pr_commit_shas(pull_request)

    # UNKNOWN: infer from the commit graph -- >=2 parents means a real merge commit.
    if not pull_request.merge_commit_sha:
        return set()
    output = run_git(
        ["-C", str(clone_dir), "rev-list", "--parents", "-n", "1", pull_request.merge_commit_sha],
        timeout=timeout,
    )
    parent_count = len(output.split()) - 1
    if parent_count >= 2:
        return _pr_commit_shas(pull_request) or {pull_request.merge_commit_sha}
    return {pull_request.merge_commit_sha}


def _sum_blame(
    clone_dir: Path, rev: str, paths: Iterable[str], commit_shas: set[str], *, timeout: float | None
) -> int:
    """Sums surviving lines per path. A path absent at `rev` (e.g. the PR deleted it) contributes
    0 -- that is the correct answer, not an error. A `blame_counts` failure only means "path
    missing" when the failure is neither a timeout nor accompanied by the path genuinely existing
    at `rev`; any other failure (a blame timeout on a huge file, a corrupt clone) is re-raised so
    the caller records a retryable `error` instead of silently under-counting `lines_at_merge`
    (round 2 review MAJOR)."""
    total = 0
    for path in paths:
        try:
            counts = blame_counts(clone_dir, rev, path, timeout=timeout)
        except GitOperationError as exc:
            if exc.reason == "timeout" or path_exists_at(clone_dir, rev, path, timeout=timeout):
                raise
            continue
        total += sum(count for sha, count in counts.items() if sha in commit_shas)
    return total


def _sum_surviving(
    clone_dir: Path,
    merge_sha: str,
    snapshot_sha: str,
    paths: Iterable[str],
    commit_shas: set[str],
    *,
    timeout: float | None,
) -> int:
    total = 0
    for path in paths:
        resolved = resolve_path_at(clone_dir, merge_sha, snapshot_sha, path, timeout=timeout)
        if resolved is None:
            continue
        counts = blame_counts(clone_dir, snapshot_sha, resolved, timeout=timeout)
        total += sum(count for sha, count in counts.items() if sha in commit_shas)
    return total


def _snapshot_sha(
    clone_dir: Path, default_branch: str, deadline: datetime.datetime, *, timeout: float | None
) -> str | None:
    before = deadline.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    output = run_git(
        ["-C", str(clone_dir), "rev-list", "-n", "1", f"--before={before}", f"refs/heads/{default_branch}"],
        timeout=timeout,
    )
    return output.strip() or None


def compute_churn_for_pull_request(
    pull_request: PullRequest,
    clone_dir: Path,
    window_days: int,
    *,
    max_files: int = 50,
    git_timeout: float | None = None,
) -> ChurnOutcome:
    """The §9 algorithm. Never raises -- any `GitOperationError` becomes an `error` outcome with a
    masked reason; one PR's failure never aborts its repository. `max_files`/`git_timeout` are
    `CHURN_MAX_FILES`/`CHURN_GIT_TIMEOUT_SECONDS`, resolved once by the caller (see module
    docstring) -- this function never reads an AppSetting itself."""
    pr_id = pull_request.id

    if pull_request.merge_method == PullRequest.MergeMethod.REBASE:
        return ChurnOutcome(pr_id, window_days, ChurnResult.Status.UNSUPPORTED_MERGE_METHOD)

    paths = [f.path for f in pull_request.files.all() if not f.is_excluded]
    if len(paths) > max_files:
        return ChurnOutcome(pr_id, window_days, ChurnResult.Status.TOO_LARGE, error=str(max_files))

    try:
        commit_shas = _pr_commit_set(pull_request, clone_dir, timeout=git_timeout)
    except GitOperationError as exc:
        return _error_outcome(pr_id, window_days, "no_pr_commits", str(exc))

    if not commit_shas:
        return _error_outcome(pr_id, window_days, "no_pr_commits", "no commits found for pull request")

    try:
        lines_at_merge = _sum_blame(
            clone_dir, pull_request.merge_commit_sha, paths, commit_shas, timeout=git_timeout
        )
    except GitOperationError as exc:
        return _error_outcome(pr_id, window_days, "blame_failed", str(exc))

    if lines_at_merge == 0:
        # A PR whose only non-excluded files were deleted (or that otherwise attributed no
        # surviving lines at merge) settles as `ok` with `churn_ratio=None` rather than being
        # left eligible forever -- spec §9.6's "never a 0% ratio" without re-cloning and
        # re-blaming this PR on every future nightly run.
        return ChurnOutcome(pr_id, window_days, ChurnResult.Status.OK, lines_at_merge=0, skip=True)

    assert pull_request.merged_at is not None  # eligible_pull_requests only yields merged PRs
    deadline = pull_request.merged_at + datetime.timedelta(days=window_days)
    try:
        snapshot_sha = _snapshot_sha(
            clone_dir, pull_request.repository.default_branch, deadline, timeout=git_timeout
        )
    except GitOperationError as exc:
        return _error_outcome(pr_id, window_days, "no_snapshot", str(exc))
    if not snapshot_sha:
        return _error_outcome(
            pr_id, window_days, "no_snapshot", "no commit on default branch before window end"
        )

    try:
        lines_surviving = _sum_surviving(
            clone_dir, pull_request.merge_commit_sha, snapshot_sha, paths, commit_shas, timeout=git_timeout
        )
    except GitOperationError as exc:
        return _error_outcome(pr_id, window_days, "blame_failed", str(exc))

    ratio = max(0.0, min(1.0, 1 - lines_surviving / lines_at_merge))

    return ChurnOutcome(
        pr_id,
        window_days,
        ChurnResult.Status.OK,
        lines_at_merge=lines_at_merge,
        lines_surviving=lines_surviving,
        churn_ratio=ratio,
        snapshot_sha=snapshot_sha,
    )


def _write_outcome(outcome: ChurnOutcome) -> None:
    """Always writes -- including a `skip` outcome (settling the PR without a bogus ratio) --
    so `update_or_create` also clears any stale `error` row left by an earlier run that is no
    longer accurate."""
    ChurnResult.objects.update_or_create(
        pull_request_id=outcome.pull_request_id,
        window_days=outcome.window_days,
        defaults={
            "status": outcome.status,
            "lines_at_merge": outcome.lines_at_merge,
            "lines_surviving": outcome.lines_surviving,
            "churn_ratio": outcome.churn_ratio,
            "snapshot_sha": outcome.snapshot_sha,
            "error": outcome.error,
        },
    )


def _tally(counts: dict[str, int], outcome: ChurnOutcome) -> None:
    if outcome.skip:
        counts["skipped"] += 1
    elif outcome.status == ChurnResult.Status.OK:
        counts["computed"] += 1
    elif outcome.status == ChurnResult.Status.TOO_LARGE:
        counts["too_large"] += 1
    elif outcome.status == ChurnResult.Status.UNSUPPORTED_MERGE_METHOD:
        counts["unsupported"] += 1
    else:
        counts["errors"] += 1


def _compute_repository(
    repository: Repository,
    pull_requests: list[PullRequest],
    window_days: int,
    repo_budget: float,
    max_files: int,
    git_timeout: float | None,
    diff_pull_requests: list[PullRequest] | None = None,
    diff_rules: list[DiffRuleSpec] | None = None,
) -> RepositoryWork:
    """Runs in a worker thread. `repo_budget` is a duration, not an absolute deadline -- the
    deadline is computed here, as the first statement, so it measures this repository's own work
    time rather than the time it spent queued behind other repositories in the pool.

    Churn comes first and diff analysis second, out of the same budget and the same clone. Churn
    has a deadline of its own (a PR whose window has elapsed and is never computed silently loses
    a metric), whereas an unanalysed diff simply waits for tomorrow night -- so when the budget
    runs out, it is diff work that is left undone."""
    deadline = time.monotonic() + repo_budget
    diff_pull_requests = diff_pull_requests or []

    try:
        auth = auth_for_connection(repository.connection)
        credentials = auth.get_git_credentials()
    except ConnectionNotUsableError:
        logger.warning("Churn: connection unusable for repository %s.", repository.full_name)
        return RepositoryWork(
            churn=[
                _error_outcome(pr.id, window_days, "connection_unusable", "connection is not usable")
                for pr in pull_requests
            ],
            diffs=[
                DiffOutcome(pull_request_id=pr.id, status=DiffAnalysis.Status.NO_CLONE)
                for pr in diff_pull_requests
            ],
        )

    try:
        clone_dir = ensure_clone(repository, credentials, timeout=git_timeout)
    except GitOperationError as exc:
        logger.warning("Churn: clone/fetch failed for repository %s.", repository.full_name)
        return RepositoryWork(
            churn=[_error_outcome(pr.id, window_days, "fetch_failed", str(exc)) for pr in pull_requests],
            diffs=[
                DiffOutcome(pull_request_id=pr.id, status=DiffAnalysis.Status.NO_CLONE)
                for pr in diff_pull_requests
            ],
        )

    outcomes = []
    for pull_request in pull_requests:
        if time.monotonic() >= deadline:
            break
        try:
            outcome = compute_churn_for_pull_request(
                pull_request, clone_dir, window_days, max_files=max_files, git_timeout=git_timeout
            )
        except Exception:
            # Belt and suspenders: compute_churn_for_pull_request's own docstring promises it
            # never raises, but this loop is the boundary that must hold even if a future bug
            # breaks that promise -- one PR's failure must never escape into run_churn's
            # whole-repository except Exception (round 2 review MAJOR).
            logger.exception(
                "Churn: unexpected failure computing pull request %s; recording error outcome.",
                pull_request.id,
            )
            outcome = _error_outcome(pull_request.id, window_days, "worker_failed", "unexpected pr failure")
        outcomes.append(outcome)

    diff_outcomes = []
    for pull_request in diff_pull_requests:
        if time.monotonic() >= deadline:
            break
        # `analyse_pull_request_diff` promises never to raise, and this loop is the boundary that
        # has to hold even if a future change breaks that promise -- one diff must never cost a
        # repository its churn results.
        try:
            diff_outcomes.append(
                analyse_pull_request_diff(
                    pull_request, clone_dir, diff_rules or [], max_files=max_files, git_timeout=git_timeout
                )
            )
        except Exception:
            logger.exception(
                "Diff analysis: unexpected failure on pull request %s; skipping it this run.",
                pull_request.id,
            )

    return RepositoryWork(churn=outcomes, diffs=diff_outcomes)


def _write_diff_outcome(outcome: DiffOutcome, rules: list[DiffRuleSpec], counts: dict[str, int]) -> None:
    """Stores one diff analysis and reconciles the pull request's diff-family signals.

    A non-`ok` outcome records the status and nothing else: no facts, and crucially no signal
    reconciliation. A missing clone or a failed `git` call says nothing about whether a signal
    still holds, so deleting the stored ones would let an unreachable repository quietly erase its
    own evidence (PLAN stage 6).
    """
    if outcome.status == DiffAnalysis.Status.OK:
        counts["diffs_analysed"] += 1
    else:
        counts["diffs_unreadable"] += 1

    DiffAnalysis.objects.update_or_create(
        pull_request_id=outcome.pull_request_id,
        defaults={
            "status": outcome.status,
            "facts": outcome.facts,
            "base_sha": outcome.base_sha,
            "head_sha": outcome.head_sha,
            "error": outcome.error,
        },
    )
    if outcome.status != DiffAnalysis.Status.OK:
        return

    written = reconcile_diff_signals(outcome.pull_request_id, outcome.matches, rules)
    counts["diff_signals_created"] += written.created
    counts["diff_signals_deleted"] += written.deleted


def run_churn(
    window_days: int | None = None,
    *,
    repo_full_names: Iterable[str] | None = None,
    project_slug: str | None = None,
    limit: int | None = None,
    analyse_diffs: bool = True,
) -> ChurnRunResult:
    if window_days is None:
        window_days = get_int("CHURN_WINDOW_DAYS")

    files_prefetch = Prefetch("files", queryset=PRFile.objects.filter(is_excluded=False))
    pull_requests = list(
        eligible_pull_requests(
            window_days, repo_full_names=repo_full_names, project_slug=project_slug, limit=limit
        ).prefetch_related(files_prefetch, "pull_request_commits__commit")
    )
    # Read every AppSetting a worker thread would otherwise need, once, here on the main thread.
    repo_budget = get_int("CHURN_REPO_TIME_BUDGET_SECONDS")
    max_workers = get_int("CHURN_MAX_WORKERS")
    max_files = get_int("CHURN_MAX_FILES")
    git_timeout = get_int("CHURN_GIT_TIMEOUT_SECONDS")

    # Diff analysis (phase 12, stage 6) is opt-in per repository and rides along here because it
    # needs the clone this run already makes. The rules, like the settings above, are flattened to
    # plain data on this thread: a worker never opens an ORM connection.
    opted_in = [str(name) for name in get_list("DIFF_ANALYSIS_REPOSITORIES")] if analyse_diffs else []
    diff_rules = diff_rule_specs() if opted_in else []
    diff_pull_requests = (
        list(
            pull_requests_needing_diff_analysis(
                opted_in, repo_full_names=repo_full_names, project_slug=project_slug, limit=limit
            ).prefetch_related(files_prefetch)
        )
        if opted_in
        else []
    )

    by_repository: dict[Repository, list[PullRequest]] = defaultdict(list)
    for pull_request in pull_requests:
        by_repository[pull_request.repository].append(pull_request)
    diffs_by_repository: dict[Repository, list[PullRequest]] = defaultdict(list)
    for pull_request in diff_pull_requests:
        diffs_by_repository[pull_request.repository].append(pull_request)

    counts: dict[str, int] = {
        "computed": 0,
        "skipped": 0,
        "too_large": 0,
        "unsupported": 0,
        "errors": 0,
        "diffs_analysed": 0,
        "diffs_unreadable": 0,
        "diff_signals_created": 0,
        "diff_signals_deleted": 0,
    }

    # One task per repository over the union of both work sets, so a repository that needs only
    # diff analysis is cloned once and a repository that needs both is cloned once too.
    repositories = list(dict.fromkeys([*by_repository, *diffs_by_repository]))

    if repositories:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _compute_repository,
                    repository,
                    by_repository.get(repository, []),
                    window_days,
                    repo_budget,
                    max_files,
                    git_timeout,
                    diffs_by_repository.get(repository, []),
                    diff_rules,
                ): repository
                for repository in repositories
            }
            for future, repository in futures.items():
                try:
                    work = future.result()
                except Exception:
                    # A repository's unexpected failure (e.g. a disk-full OSError from
                    # ensure_clone) must never abort the other repositories' results, nor lose
                    # bump_data_version() -- spec §5.3's rule, reused (RISKS row 11). The diff
                    # half records nothing in that case: an unanalysed diff is simply retried
                    # tomorrow, whereas a churn window that has elapsed must be settled.
                    logger.exception(
                        "Churn: repository %s failed unexpectedly; recording error outcomes.",
                        repository.full_name,
                    )
                    work = RepositoryWork(
                        churn=[
                            _error_outcome(pr.id, window_days, "worker_failed", "unexpected worker failure")
                            for pr in by_repository.get(repository, [])
                        ]
                    )
                for outcome in work.churn:
                    _write_outcome(outcome)
                    _tally(counts, outcome)
                for diff_outcome in work.diffs:
                    _write_diff_outcome(diff_outcome, diff_rules, counts)

    bump_data_version()
    return ChurnRunResult(**counts)
