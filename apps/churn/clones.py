"""Bare-clone lifecycle for churn's git work (spec §9, ARCHITECTURE "git / churn": the clone
directory is disposable). Every call goes through `run_git()`, the project's only subprocess call
site."""

from __future__ import annotations

import shutil
from pathlib import Path

from django.conf import settings

from apps.catalog.models import Repository
from apps.churn.gitcmd import GitOperationError, run_git


def clone_dir(repository: Repository) -> Path:
    owner, name = repository.full_name.split("/", 1)
    return Path(settings.DATA_DIR) / "repos" / owner / f"{name}.git"


def remote_url_for(repository: Repository) -> str:
    return f"https://github.com/{repository.full_name}.git"


def _clone(
    repository: Repository, target: Path, credentials: tuple[str, str] | None, timeout: float | None
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    run_git(
        ["clone", "--bare", "--quiet", remote_url_for(repository), str(target)],
        credentials=credentials,
        timeout=timeout,
    )


def _fetch(target: Path, credentials: tuple[str, str] | None, timeout: float | None) -> None:
    run_git(
        ["-C", str(target), "fetch", "--prune", "--quiet", "origin", "+refs/heads/*:refs/heads/*"],
        credentials=credentials,
        timeout=timeout,
    )


def ensure_clone(
    repository: Repository, credentials: tuple[str, str] | None, *, timeout: float | None = None
) -> Path:
    """Bare clone if absent, otherwise fetch. A failing fetch deletes the directory and
    re-clones exactly once; a second failure raises `GitOperationError` with a masked reason.
    `timeout` is `CHURN_GIT_TIMEOUT_SECONDS`, read once by the caller (RISKS row 14: a worker
    thread must never open its own ORM connection to read a setting)."""
    target = clone_dir(repository)

    if not target.exists():
        _clone(repository, target, credentials, timeout)
        return target

    try:
        _fetch(target, credentials, timeout)
    except GitOperationError:
        shutil.rmtree(target, ignore_errors=True)
        _clone(repository, target, credentials, timeout)

    return target
