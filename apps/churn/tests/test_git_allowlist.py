"""The `git`-side half of the read-only guarantee, pinned. `github_sync` proves it for the API by
checking every GraphQL document and hardcoding GET on REST; `run_git()` is the third way out of the
process, and these tests are what keeps a `push` from being added to it without anyone noticing."""

import subprocess

import pytest

from apps.churn.gitcmd import (
    _REMOTE_SUBCOMMANDS,
    ALLOWED_SUBCOMMANDS,
    GitCommandNotAllowedError,
    GitOperationError,
    assert_allowed,
    run_git,
    subcommand_of,
)

#: Every `git` subcommand that can put something on a remote. None of them may ever be allowlisted.
REMOTE_WRITERS = ["push", "send-pack", "send-email", "request-pull", "svn", "p4", "subtree"]


def test_only_clone_and_fetch_ever_reach_a_remote():
    """Pinned as an equality rather than a subset: a third network subcommand is a change of what
    PR Radar does to GitHub, and it should fail here first."""
    assert _REMOTE_SUBCOMMANDS == {"clone", "fetch"}


@pytest.mark.parametrize("subcommand", REMOTE_WRITERS)
def test_no_remote_write_subcommand_is_allowed(subcommand):
    assert subcommand not in ALLOWED_SUBCOMMANDS
    with pytest.raises(GitCommandNotAllowedError):
        assert_allowed(["-C", "/tmp/clone", subcommand, "origin", "main"])


def test_run_git_refuses_a_push_without_spawning_anything(monkeypatch):
    def _fail(*args, **kwargs):
        raise AssertionError("subprocess.run must not be reached for a forbidden subcommand")

    monkeypatch.setattr(subprocess, "run", _fail)

    with pytest.raises(GitCommandNotAllowedError) as exc_info:
        run_git(["-C", "/tmp/clone", "push", "origin", "main"], credentials=("x-access-token", "ghp_t"))

    assert exc_info.value.subcommand == "push"


def test_a_forbidden_subcommand_is_not_a_git_operation_error():
    """`blame.path_exists_at()` turns a `GitOperationError` into "the path is not there". A bug in
    PR Radar must not be able to disguise itself as an answer about a repository."""
    with pytest.raises(GitCommandNotAllowedError):
        assert_allowed(["push"])
    assert not issubclass(GitCommandNotAllowedError, GitOperationError)


@pytest.mark.parametrize(
    "args,expected",
    [
        (["clone", "--bare", "https://github.com/acme/widget.git", "/tmp/w"], "clone"),
        (["-C", "/tmp/w", "fetch", "--prune", "origin"], "fetch"),
        (["-C", "/tmp/w", "-c", "user.name=Churn Test", "commit", "-m", "x"], "commit"),
    ],
)
def test_subcommand_is_read_past_the_leading_global_options(args, expected):
    assert subcommand_of(args) == expected
    assert_allowed(args)


def test_an_option_after_the_subcommand_is_left_alone():
    """`blame -M -C <rev>` reuses `-C` for rename detection, nothing like `git -C <path>`. Only the
    tokens before the subcommand belong to `git` itself."""
    assert subcommand_of(["-C", "/tmp/w", "blame", "--line-porcelain", "-M", "-C", "HEAD"]) == "blame"


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["-C"],
        ["--exec-path=/tmp/evil", "status"],
        ["--git-dir=/tmp/w", "status"],
    ],
    ids=["empty", "dangling-value", "unknown-option", "unlisted-option"],
)
def test_anything_unrecognised_before_the_subcommand_is_rejected(args):
    """Fail closed: an unrecognised leading token could hide the real subcommand from the check,
    so it is refused rather than skipped."""
    with pytest.raises(GitCommandNotAllowedError):
        assert_allowed(args)


@pytest.mark.parametrize(
    "args",
    [
        ["-C", "/tmp/w", "rev-list", "--parents", "-n", "1", "HEAD"],
        ["-C", "/tmp/w", "cat-file", "-e", "HEAD:a.py"],
        ["-C", "/tmp/w", "log", "--name-status", "-M"],
        ["-C", "/tmp/w", "show", "HEAD:a.py"],
        ["-C", "/tmp/w", "diff", "--numstat", "base", "head"],
    ],
)
def test_the_shipped_read_calls_pass(args):
    """The exact shapes `churn` sends today, so a tightening of the allowlist that breaks the
    product fails here rather than at the first sync."""
    assert_allowed(args)
