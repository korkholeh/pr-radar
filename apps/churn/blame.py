"""`git blame` parsing and rename resolution for the churn algorithm (spec §9.4/§9.5).

`--line-porcelain` guarantees one header line per blamed line (unlike plain `--porcelain`, which
elides the header for repeated commits) -- so counting header lines is exactly counting lines,
each attributed to the commit that introduced it."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from apps.churn.gitcmd import GitOperationError, run_git

_HEADER_RE = re.compile(r"^[0-9a-f]{40,64} \d+ \d+(?: \d+)?$")


def blame_counts(clone: Path, rev: str, path: str, *, timeout: float | None = None) -> Counter[str]:
    """Line counts per commit sha for `path` at `rev`. Raises `GitOperationError` if `path`
    does not exist at `rev` -- callers resolve existence (e.g. via `resolve_path_at`) first."""
    output = run_git(
        ["-C", str(clone), "blame", "--line-porcelain", "-M", "-C", rev, "--", path], timeout=timeout
    )
    counts: Counter[str] = Counter()
    current_sha: str | None = None
    for line in output.splitlines():
        if _HEADER_RE.match(line):
            current_sha = line.split(" ", 1)[0]
        elif line.startswith("\t") and current_sha is not None:
            counts[current_sha] += 1
    return counts


def path_exists_at(clone: Path, rev: str, path: str, *, timeout: float | None = None) -> bool:
    """`True` iff `path` exists at `rev`. A `cat-file -e` failure is treated as "does not exist"
    -- that is exactly what a clean, quick exit code means; a genuinely slow or broken clone still
    surfaces as `reason="timeout"` to a caller that checks it separately (see `_sum_blame`)."""
    try:
        run_git(["-C", str(clone), "cat-file", "-e", f"{rev}:{path}"], timeout=timeout)
    except GitOperationError:
        return False
    return True


def resolve_path_at(
    clone: Path, from_rev: str, to_rev: str, path: str, *, timeout: float | None = None
) -> str | None:
    """`path`'s name at `to_rev`, following renames recorded between `from_rev` and `to_rev`.
    Tries the original path first; on a miss, walks `R<score>\\t<old>\\t<new>` entries from
    `git log --name-status -M --find-renames`, applied oldest-first. Returns `None` when the
    path (renamed or not) does not exist at `to_rev` -- e.g. it was deleted."""
    if path_exists_at(clone, to_rev, path, timeout=timeout):
        return path

    output = run_git(
        [
            "-C",
            str(clone),
            "log",
            "--name-status",
            "-M",
            "--find-renames",
            "--format=%H",
            "--reverse",
            f"{from_rev}..{to_rev}",
        ],
        timeout=timeout,
    )
    current = path
    for line in output.splitlines():
        if not line.startswith("R"):
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        _score, old, new = parts
        if old == current:
            current = new

    if current != path and path_exists_at(clone, to_rev, current, timeout=timeout):
        return current
    return None
