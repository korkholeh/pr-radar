"""The only `subprocess` call site in the project (ADR 0004, RISKS row 2). The token reaches
`git` exclusively through `GIT_ASKPASS` + two env vars read by `askpass.py`; it is never placed
in `args`, a remote URL, or an exception message -- `run_git()` masks `stderr` through the same
function the logger uses before it ever reaches a caller.

Every invocation is checked against `ALLOWED_SUBCOMMANDS` first, so the process never learns a
`git` verb PR Radar has not agreed to run -- the `git`-side half of the read-only guarantee that
`github_sync.client.assert_read_only()` keeps on the API side."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from config.security import mask_secrets

ASKPASS_PATH = Path(__file__).resolve().parent / "askpass.py"


class GitOperationError(Exception):
    def __init__(self, message: str, *, reason: str = "git_error") -> None:
        super().__init__(message)
        self.reason = reason


class GitCommandNotAllowedError(Exception):
    """A `git` subcommand outside the allowlist below. Deliberately *not* a `GitOperationError`:
    callers such as `blame.path_exists_at()` swallow that one to mean "the path is not there", and
    a forbidden command is a bug in PR Radar, not an answer about a repository. It has to be loud."""

    def __init__(self, subcommand: str) -> None:
        super().__init__(f"git subcommand {subcommand!r} is not in the PR Radar allowlist.")
        self.subcommand = subcommand


#: The only two subcommands that talk to GitHub, and both of them only read from it. The read-only
#: guarantee (ADR 0003) is checked on the GraphQL document and on the REST verb in `github_sync`;
#: `git` is the third way out of the process, and this is where the same promise is pinned. Nothing
#: that can write to a remote -- `push`, `send-pack`, `send-email`, `subtree push`, `svn`, `p4` --
#: is here, and adding one is a decision, not a refactor.
_REMOTE_SUBCOMMANDS = frozenset({"clone", "fetch"})

#: Reads against a local clone. No network, no writes.
_LOCAL_READ_SUBCOMMANDS = frozenset(
    {
        "blame",
        "cat-file",
        "config",
        "diff",
        "log",
        "ls-tree",
        "remote",
        "rev-list",
        "rev-parse",
        "show",
        "status",
    }
)

#: Writes confined to a local repository. Shipped code uses none of these; the churn test fixtures
#: build throwaway repositories with them, and they go through `run_git()` rather than a second
#: `subprocess` call site so that ADR 0004's "one call site" invariant survives the test suite.
_LOCAL_WRITE_SUBCOMMANDS = frozenset({"add", "checkout", "commit", "init", "merge", "mv"})

ALLOWED_SUBCOMMANDS = _REMOTE_SUBCOMMANDS | _LOCAL_READ_SUBCOMMANDS | _LOCAL_WRITE_SUBCOMMANDS

#: Options that may precede the subcommand, each taking a separate value (`-C <path>`,
#: `-c <name>=<value>`). Anything else before the subcommand is rejected rather than guessed at:
#: an unrecognised leading token could hide the real subcommand from this check.
_VALUE_GLOBAL_OPTIONS = frozenset({"-C", "-c"})


def subcommand_of(args: Sequence[str]) -> str:
    """The subcommand in `args`, skipping the leading global options. Options that appear *after*
    the subcommand are not examined -- `blame -M -C <rev>` reuses `-C` with an entirely different
    meaning, and only the tokens before the subcommand are git's own."""
    index = 0
    while index < len(args) and args[index].startswith("-"):
        if args[index] not in _VALUE_GLOBAL_OPTIONS:
            raise GitCommandNotAllowedError(args[index])
        index += 2
    if index >= len(args):
        raise GitCommandNotAllowedError("")
    return args[index]


def assert_allowed(args: Sequence[str]) -> None:
    """Refuses any `git` invocation whose subcommand is not in `ALLOWED_SUBCOMMANDS`."""
    subcommand = subcommand_of(args)
    if subcommand not in ALLOWED_SUBCOMMANDS:
        raise GitCommandNotAllowedError(subcommand)


def git_env(credentials: tuple[str, str] | None = None) -> dict[str, str]:
    """The subprocess environment for a `git` call. `GIT_TERMINAL_PROMPT=0` keeps a background
    job from ever blocking on a TTY; `GIT_CONFIG_NOSYSTEM`/`GIT_CONFIG_GLOBAL=/dev/null` keep the
    operator's own credential helper from being offered (and storing) our token; `GIT_ADVICE=0`
    and `LC_ALL=C` keep stderr parseable and stable across locales. `credentials`, when given, is
    `(username, token)` from `GitHubAuth.get_git_credentials()` -- added only for `clone`/`fetch`,
    never for a credential-free call such as `blame`, `rev-list` or `log`."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_ADVICE"] = "0"
    env["LC_ALL"] = "C"
    if credentials is not None:
        username, token = credentials
        env["GIT_ASKPASS"] = str(ASKPASS_PATH)
        env["PR_RADAR_GIT_USERNAME"] = username
        env["PR_RADAR_GIT_TOKEN"] = token
    return env


def run_git(
    args: Sequence[str],
    *,
    cwd: Path | str | None = None,
    credentials: tuple[str, str] | None = None,
    timeout: float | None = None,
) -> str:
    """Runs `git <args>` and returns stdout. Never interpolates `credentials` into `args` --
    the token reaches the process only via `git_env()`'s environment. Raises `GitOperationError`
    with a masked message on a non-zero exit or a timeout (`reason="timeout"`), and
    `GitCommandNotAllowedError` -- before spawning anything -- for a subcommand off the allowlist."""
    assert_allowed(args)
    env = git_env(credentials)
    try:
        result = subprocess.run(  # noqa: S603 -- the only subprocess call site (ADR 0004)
            ["git", *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitOperationError(mask_secrets(str(exc)), reason="timeout") from exc

    if result.returncode != 0:
        detail = mask_secrets(result.stderr.strip() or f"git {' '.join(args)} exited {result.returncode}")
        if credentials is not None:
            token = credentials[1]
            if token:
                detail = detail.replace(token, f"***{token[-4:]}")
        raise GitOperationError(detail, reason="git_error")

    return result.stdout
