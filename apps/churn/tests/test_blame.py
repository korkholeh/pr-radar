"""`apps/churn/blame.py` against the hand-countable `git_origin` fixture (T9)."""

from __future__ import annotations

import datetime

import pytest

from apps.churn.blame import blame_counts, resolve_path_at
from apps.churn.gitcmd import GitOperationError, run_git

UTC = datetime.UTC


def test_blame_counts_matches_hand_counted_lines_at_merge_and_snapshot(git_origin):
    at_merge = blame_counts(git_origin.path, git_origin.squash_sha, "a.py")
    assert sum(at_merge.values()) == 10
    assert at_merge[git_origin.squash_sha] == 10

    at_snapshot = blame_counts(git_origin.path, git_origin.final_sha, "a.py")
    assert at_snapshot[git_origin.squash_sha] == 6
    assert sum(at_snapshot.values()) == 10


def test_blame_counts_for_a_merge_commit_attributes_each_file_to_its_own_commit(git_origin):
    at_merge_b = blame_counts(git_origin.path, git_origin.merge_sha, "b.py")
    at_merge_c = blame_counts(git_origin.path, git_origin.merge_sha, "c.py")
    assert at_merge_b[git_origin.c1_sha] == 6
    assert at_merge_c[git_origin.c2_sha] == 4

    at_snapshot_b = blame_counts(git_origin.path, git_origin.final_sha, "b.py")
    at_snapshot_c = blame_counts(git_origin.path, git_origin.final_sha, "c.py")
    surviving = at_snapshot_b[git_origin.c1_sha] + at_snapshot_c[git_origin.c2_sha]
    assert surviving == 5


def test_resolve_path_at_returns_original_path_when_unchanged(git_origin):
    resolved = resolve_path_at(git_origin.path, git_origin.squash_sha, git_origin.final_sha, "a.py")
    assert resolved == "a.py"


def test_resolve_path_at_follows_a_rename(git_origin):
    resolved = resolve_path_at(git_origin.path, git_origin.rename_squash_sha, git_origin.final_sha, "d.py")
    assert resolved == "e.py"

    at_snapshot = blame_counts(git_origin.path, git_origin.final_sha, resolved)
    assert at_snapshot[git_origin.rename_squash_sha] == 8


def test_resolve_path_at_returns_none_for_a_deleted_path(tmp_path):
    repo_dir = tmp_path / "repo"
    run_git(["init", "--quiet", "-b", "main", str(repo_dir)])
    identity = ["-c", "user.name=Churn Test", "-c", "user.email=churn-test@example.com"]

    (repo_dir / "gone.py").write_text("one\ntwo\n")
    run_git(["-C", str(repo_dir), "add", "-A"])
    run_git(["-C", str(repo_dir), *identity, "commit", "-m", "add gone.py"])
    from_sha = run_git(["-C", str(repo_dir), "rev-parse", "HEAD"]).strip()

    (repo_dir / "gone.py").unlink()
    run_git(["-C", str(repo_dir), "add", "-A"])
    run_git(["-C", str(repo_dir), *identity, "commit", "-m", "delete gone.py"])
    to_sha = run_git(["-C", str(repo_dir), "rev-parse", "HEAD"]).strip()

    resolved = resolve_path_at(repo_dir, from_sha, to_sha, "gone.py")
    assert resolved is None


def test_blame_counts_raises_on_a_missing_path(git_origin):
    with pytest.raises(GitOperationError):
        blame_counts(git_origin.path, git_origin.final_sha, "does/not/exist.py")
