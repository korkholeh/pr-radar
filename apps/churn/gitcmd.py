"""The only `subprocess` call site in the project (ADR 0004, RISKS row 2). The token reaches
`git` exclusively through `GIT_ASKPASS` + two env vars read by `askpass.py`; it is never placed
in `args`, a remote URL, or an exception message -- `run_git()` masks `stderr` through the same
function the logger uses before it ever reaches a caller."""

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
    with a masked message on a non-zero exit or a timeout (`reason="timeout"`)."""
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
