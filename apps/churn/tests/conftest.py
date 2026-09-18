"""Churn is the one component that cannot be tested against JSON fixtures (spec §9): a
session-scoped `git_origin` fixture builds a real temporary git repository with a hand-countable
history, and `remote_url_for` is monkeypatched to a `file://` URL pointing at it so
`ensure_clone()` exercises the real `clone`/`fetch` path with no network and no credential.

Layout of the shared history on `main` (all timestamps UTC, `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE`
pinned so `--before=` snapshot lookups are deterministic):

    2020-01-01  initial commit (README.md)
    2020-01-02  squash_sha:        a.py, 10 lines               (squash_pr's merge commit)
    2020-01-03  rewrite 4 of those 10 lines in a.py
    2020-01-10  c1_sha:            b.py, 6 lines   (on feature-merge)
    2020-01-10  c2_sha:            c.py, 4 lines   (on feature-merge)
    2020-01-11  merge_sha:         merge feature-merge into main, --no-ff  (merge_pr's merge commit)
    2020-01-12  delete 5 of b.py's 6 lines, leaving 1
    2020-01-20  rename_squash_sha: d.py, 8 lines               (rename_pr's merge commit)
    2020-01-21  git mv d.py e.py (content unchanged)
    2020-01-24  add h.py, 5 lines (baseline, not itself a PR)
    2020-01-25  mixed_delete_sha:  i.py, 3 lines; deletes h.py  (mixed_delete_pr's merge commit)
    2020-01-30  zero_sha:          generated/gen.py, 3 lines   (zero_pr's merge commit; excluded in DB)
    2020-02-01  final_sha:         trailing commit, so every PR above has a snapshot after its rewrite

Hand-counted outcomes (window = 21 days, `CHURN_WINDOW_DAYS`'s default):
    squash_pr : lines_at_merge=10, lines_surviving=6  (lines 3-6 were overwritten)
    merge_pr  : lines_at_merge=10, lines_surviving=5  (1 left in b.py + 4 in c.py)
    rename_pr : lines_at_merge=8,  lines_surviving=8  (renamed, content untouched)
    zero_pr   : lines_at_merge=0   (its only file is excluded)
    mixed_delete_pr : lines_at_merge=3, lines_surviving=3  (i.py added, h.py deleted in the same
        commit -- h.py must not exist at merge_commit_sha, proving a deleted non-excluded file
        contributes 0 rather than failing the whole PR into status=error)
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from apps.activity.factories import (
    CommitFactory,
    PRFileFactory,
    PullRequestCommitFactory,
    PullRequestFactory,
)
from apps.activity.models import PullRequest
from apps.churn.gitcmd import run_git

UTC = datetime.UTC
_GIT_IDENTITY = ["-c", "user.name=Churn Test", "-c", "user.email=churn-test@example.com"]


def _write_files(repo_dir: Path, files: dict[str, str]) -> None:
    for path, content in files.items():
        full = repo_dir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)


def _commit(
    repo_dir: Path,
    message: str,
    when: datetime.datetime,
    files: dict[str, str] | None = None,
    deletes: list[str] | None = None,
) -> str:
    if files:
        _write_files(repo_dir, files)
    for path in deletes or []:
        (repo_dir / path).unlink()
    run_git(["-C", str(repo_dir), "add", "-A"])
    date_str = when.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    previous = {key: os.environ.get(key) for key in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE")}
    os.environ["GIT_AUTHOR_DATE"] = date_str
    os.environ["GIT_COMMITTER_DATE"] = date_str
    try:
        run_git(["-C", str(repo_dir), *_GIT_IDENTITY, "commit", "--allow-empty", "-m", message])
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return run_git(["-C", str(repo_dir), "rev-parse", "HEAD"]).strip()


def _lines(prefix: str, count: int) -> str:
    return "".join(f"{prefix}{i}\n" for i in range(1, count + 1))


@dataclass(frozen=True)
class GitOrigin:
    path: Path
    squash_sha: str
    squash_merged_at: datetime.datetime
    c1_sha: str
    c2_sha: str
    merge_sha: str
    merge_merged_at: datetime.datetime
    rename_squash_sha: str
    rename_merged_at: datetime.datetime
    mixed_delete_sha: str
    mixed_delete_merged_at: datetime.datetime
    zero_sha: str
    zero_merged_at: datetime.datetime
    final_sha: str


@pytest.fixture(scope="session")
def git_origin(tmp_path_factory: pytest.TempPathFactory) -> GitOrigin:
    repo_dir = tmp_path_factory.mktemp("churn_origin")
    run_git(["init", "--quiet", "-b", "main", str(repo_dir)])

    t0 = datetime.datetime(2020, 1, 1, tzinfo=UTC)
    t1 = datetime.datetime(2020, 1, 2, tzinfo=UTC)
    t2 = datetime.datetime(2020, 1, 3, tzinfo=UTC)
    t3 = datetime.datetime(2020, 1, 10, tzinfo=UTC)
    t4 = datetime.datetime(2020, 1, 10, 0, 5, tzinfo=UTC)
    t5 = datetime.datetime(2020, 1, 11, tzinfo=UTC)
    t6 = datetime.datetime(2020, 1, 12, tzinfo=UTC)
    t7 = datetime.datetime(2020, 1, 20, tzinfo=UTC)
    t8 = datetime.datetime(2020, 1, 21, tzinfo=UTC)
    t8b = datetime.datetime(2020, 1, 24, tzinfo=UTC)
    t8c = datetime.datetime(2020, 1, 25, tzinfo=UTC)
    t9 = datetime.datetime(2020, 1, 30, tzinfo=UTC)
    t10 = datetime.datetime(2020, 2, 1, tzinfo=UTC)

    _commit(repo_dir, "initial", t0, {"README.md": "init\n"})

    squash_sha = _commit(repo_dir, "Add a.py (squash)", t1, {"a.py": _lines("line", 10)})

    rewritten_a_py = _lines("line", 10).splitlines(keepends=True)
    for index in (2, 3, 4, 5):  # lines 3..6 (0-indexed 2..5)
        rewritten_a_py[index] = f"rewritten{index + 1}\n"
    _commit(repo_dir, "Rewrite some of a.py", t2, {"a.py": "".join(rewritten_a_py)})

    run_git(["-C", str(repo_dir), "checkout", "-b", "feature-merge", "main"])
    c1_sha = _commit(repo_dir, "Add b.py", t3, {"b.py": _lines("bline", 6)})
    c2_sha = _commit(repo_dir, "Add c.py", t4, {"c.py": _lines("cline", 4)})
    run_git(["-C", str(repo_dir), "checkout", "main"])

    date_str = t5.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    os.environ["GIT_AUTHOR_DATE"] = date_str
    os.environ["GIT_COMMITTER_DATE"] = date_str
    try:
        run_git(
            [
                "-C",
                str(repo_dir),
                *_GIT_IDENTITY,
                "merge",
                "--no-ff",
                "-m",
                "Merge feature-merge",
                "feature-merge",
            ]
        )
    finally:
        os.environ.pop("GIT_AUTHOR_DATE", None)
        os.environ.pop("GIT_COMMITTER_DATE", None)
    merge_sha = run_git(["-C", str(repo_dir), "rev-parse", "HEAD"]).strip()

    _commit(repo_dir, "Delete most of b.py", t6, {"b.py": "bline6\n"})

    rename_squash_sha = _commit(repo_dir, "Add d.py (squash)", t7, {"d.py": _lines("dline", 8)})

    run_git(["-C", str(repo_dir), "mv", "d.py", "e.py"])
    _commit(repo_dir, "Rename d.py to e.py", t8)

    _commit(repo_dir, "Add h.py (baseline)", t8b, {"h.py": _lines("hline", 5)})
    mixed_delete_sha = _commit(
        repo_dir, "Add i.py, delete h.py (squash)", t8c, {"i.py": _lines("iline", 3)}, deletes=["h.py"]
    )

    zero_sha = _commit(repo_dir, "Add generated file", t9, {"generated/gen.py": _lines("gline", 3)})

    final_sha = _commit(repo_dir, "Final trailing commit", t10, {"TRAILING.md": "end\n"})

    return GitOrigin(
        path=repo_dir,
        squash_sha=squash_sha,
        squash_merged_at=t1,
        c1_sha=c1_sha,
        c2_sha=c2_sha,
        merge_sha=merge_sha,
        merge_merged_at=t5,
        rename_squash_sha=rename_squash_sha,
        rename_merged_at=t7,
        mixed_delete_sha=mixed_delete_sha,
        mixed_delete_merged_at=t8c,
        zero_sha=zero_sha,
        zero_merged_at=t9,
        final_sha=final_sha,
    )


@pytest.fixture
def origin_remote(monkeypatch: pytest.MonkeyPatch, git_origin: GitOrigin) -> GitOrigin:
    """Points `apps.churn.clones.remote_url_for` at the shared origin so `ensure_clone()`
    exercises a real `clone`/`fetch` with no network and no credential."""
    import apps.churn.clones as clones_module

    monkeypatch.setattr(clones_module, "remote_url_for", lambda repository: f"file://{git_origin.path}")
    return git_origin


def make_squash_pr(repository, git_origin: GitOrigin, **overrides) -> PullRequest:
    overrides.setdefault("state", PullRequest.State.MERGED)
    overrides.setdefault("merge_method", PullRequest.MergeMethod.SQUASH)
    overrides.setdefault("merge_commit_sha", git_origin.squash_sha)
    overrides.setdefault("merged_at", git_origin.squash_merged_at)
    pr = PullRequestFactory(repository=repository, **overrides)
    PRFileFactory(pull_request=pr, path="a.py")
    return pr


def make_merge_pr(repository, git_origin: GitOrigin, **overrides) -> PullRequest:
    overrides.setdefault("state", PullRequest.State.MERGED)
    overrides.setdefault("merge_method", PullRequest.MergeMethod.MERGE)
    overrides.setdefault("merge_commit_sha", git_origin.merge_sha)
    overrides.setdefault("merged_at", git_origin.merge_merged_at)
    pr = PullRequestFactory(repository=repository, **overrides)
    PRFileFactory(pull_request=pr, path="b.py")
    PRFileFactory(pull_request=pr, path="c.py")
    c1 = CommitFactory(repository=repository, sha=git_origin.c1_sha)
    c2 = CommitFactory(repository=repository, sha=git_origin.c2_sha)
    PullRequestCommitFactory(pull_request=pr, commit=c1, position=0)
    PullRequestCommitFactory(pull_request=pr, commit=c2, position=1)
    return pr


def make_rebase_pr(repository, **overrides) -> PullRequest:
    overrides.setdefault("state", PullRequest.State.MERGED)
    overrides.setdefault("merge_method", PullRequest.MergeMethod.REBASE)
    overrides.setdefault("merged_at", datetime.datetime(2020, 3, 1, tzinfo=UTC))
    pr = PullRequestFactory(repository=repository, **overrides)
    PRFileFactory(pull_request=pr, path="f.py")
    return pr


def make_rename_pr(repository, git_origin: GitOrigin, **overrides) -> PullRequest:
    overrides.setdefault("state", PullRequest.State.MERGED)
    overrides.setdefault("merge_method", PullRequest.MergeMethod.SQUASH)
    overrides.setdefault("merge_commit_sha", git_origin.rename_squash_sha)
    overrides.setdefault("merged_at", git_origin.rename_merged_at)
    pr = PullRequestFactory(repository=repository, **overrides)
    PRFileFactory(pull_request=pr, path="d.py")
    return pr


def make_mixed_delete_pr(repository, git_origin: GitOrigin, **overrides) -> PullRequest:
    """A squash PR whose merge commit both adds `i.py` and deletes `h.py` -- `h.py`'s `PRFile`
    row is non-excluded but the path does not exist at `merge_commit_sha`, the case a deleted
    file must not turn into `status=error` for the whole PR."""
    overrides.setdefault("state", PullRequest.State.MERGED)
    overrides.setdefault("merge_method", PullRequest.MergeMethod.SQUASH)
    overrides.setdefault("merge_commit_sha", git_origin.mixed_delete_sha)
    overrides.setdefault("merged_at", git_origin.mixed_delete_merged_at)
    pr = PullRequestFactory(repository=repository, **overrides)
    PRFileFactory(pull_request=pr, path="i.py", status="added")
    PRFileFactory(pull_request=pr, path="h.py", status="deleted")
    return pr


def make_zero_pr(repository, git_origin: GitOrigin, **overrides) -> PullRequest:
    overrides.setdefault("state", PullRequest.State.MERGED)
    overrides.setdefault("merge_method", PullRequest.MergeMethod.SQUASH)
    overrides.setdefault("merge_commit_sha", git_origin.zero_sha)
    overrides.setdefault("merged_at", git_origin.zero_merged_at)
    pr = PullRequestFactory(repository=repository, **overrides)
    PRFileFactory(pull_request=pr, path="generated/gen.py", is_excluded=True)
    return pr
