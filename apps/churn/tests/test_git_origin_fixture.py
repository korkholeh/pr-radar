"""A broken `git_origin` fixture must fail loudly here, not make every churn test vacuous."""

import re
from collections import Counter

from apps.churn.gitcmd import run_git

_SHA_LINE_RE = re.compile(r"^[0-9a-f]{40} ")


def _blame_sha_counts(repo_dir, rev, path) -> Counter:
    output = run_git(["-C", str(repo_dir), "blame", "-M", "-C", "--line-porcelain", rev, "--", path])
    counts: Counter = Counter()
    for line in output.splitlines():
        if _SHA_LINE_RE.match(line):
            counts[line.split(" ", 1)[0]] += 1
    return counts


def test_squash_pr_hand_counted_lines(git_origin):
    at_merge = _blame_sha_counts(git_origin.path, git_origin.squash_sha, "a.py")
    assert sum(at_merge.values()) == 10

    at_snapshot = _blame_sha_counts(git_origin.path, git_origin.final_sha, "a.py")
    assert at_snapshot[git_origin.squash_sha] == 6
    assert sum(at_snapshot.values()) == 10


def test_merge_pr_hand_counted_lines(git_origin):
    at_merge_b = _blame_sha_counts(git_origin.path, git_origin.merge_sha, "b.py")
    at_merge_c = _blame_sha_counts(git_origin.path, git_origin.merge_sha, "c.py")
    assert sum(at_merge_b.values()) + sum(at_merge_c.values()) == 10
    assert at_merge_b[git_origin.c1_sha] == 6
    assert at_merge_c[git_origin.c2_sha] == 4

    at_snapshot_b = _blame_sha_counts(git_origin.path, git_origin.final_sha, "b.py")
    at_snapshot_c = _blame_sha_counts(git_origin.path, git_origin.final_sha, "c.py")
    surviving = at_snapshot_b[git_origin.c1_sha] + at_snapshot_c[git_origin.c2_sha]
    assert surviving == 5


def test_rename_pr_hand_counted_lines(git_origin):
    at_merge = _blame_sha_counts(git_origin.path, git_origin.rename_squash_sha, "d.py")
    assert sum(at_merge.values()) == 8
    assert at_merge[git_origin.rename_squash_sha] == 8

    at_snapshot = _blame_sha_counts(git_origin.path, git_origin.final_sha, "e.py")
    assert at_snapshot[git_origin.rename_squash_sha] == 8


def test_mixed_delete_pr_hand_counted_lines(git_origin):
    at_merge_i = _blame_sha_counts(git_origin.path, git_origin.mixed_delete_sha, "i.py")
    assert sum(at_merge_i.values()) == 3
    assert at_merge_i[git_origin.mixed_delete_sha] == 3

    # h.py must not exist at the merge commit -- it was deleted in the same commit.
    ls_tree = run_git(
        ["-C", str(git_origin.path), "ls-tree", "-r", "--name-only", git_origin.mixed_delete_sha]
    )
    assert "h.py" not in ls_tree.splitlines()


def test_zero_pr_merge_commit_exists_and_default_branch_reaches_final(git_origin):
    log = run_git(["-C", str(git_origin.path), "log", "--format=%H", "main"])
    shas = log.split()
    assert git_origin.zero_sha in shas
    assert shas[0] == git_origin.final_sha
