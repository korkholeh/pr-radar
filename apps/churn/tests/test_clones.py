"""`apps/churn/clones.py`: bare-clone lifecycle against a real, local `file://` origin
(`apps/churn/tests/conftest.py::origin_remote`) -- no network, no credential."""

from __future__ import annotations

import pytest

from apps.catalog.factories import RepositoryFactory
from apps.churn.clones import clone_dir, ensure_clone
from apps.churn.gitcmd import GitOperationError, run_git

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _data_dir_in_tmp_path(settings, tmp_path):
    settings.DATA_DIR = tmp_path


def test_clone_path_and_second_call_fetches_instead_of_recloning(origin_remote, tmp_path):
    repository = RepositoryFactory(full_name="acme/widgets")

    target = ensure_clone(repository, credentials=None)

    assert target == clone_dir(repository)
    assert target == tmp_path / "repos" / "acme" / "widgets.git"
    assert target.is_dir()
    assert (target / "HEAD").exists()

    created_at = (target / "HEAD").stat().st_mtime
    second_target = ensure_clone(repository, credentials=None)

    assert second_target == target
    # A fetch does not recreate the bare repository's HEAD file.
    assert (target / "HEAD").stat().st_mtime == created_at


def test_failing_fetch_deletes_and_reclones_once(origin_remote, monkeypatch, tmp_path):
    repository = RepositoryFactory(full_name="acme/widgets")
    ensure_clone(repository, credentials=None)

    calls = {"fetch": 0}
    real_run_git = run_git

    def flaky_run_git(args, **kwargs):
        if "fetch" in args:
            calls["fetch"] += 1
            raise GitOperationError("simulated fetch failure", reason="git_error")
        return real_run_git(args, **kwargs)

    monkeypatch.setattr("apps.churn.clones.run_git", flaky_run_git)

    target = ensure_clone(repository, credentials=None)

    assert calls["fetch"] == 1
    assert target == clone_dir(repository)
    assert target.is_dir()
    assert (target / "HEAD").exists()


def test_ensure_clone_forwards_timeout_to_the_clone_call(origin_remote, monkeypatch, tmp_path):
    repository = RepositoryFactory(full_name="acme/timeout-widgets")
    captured = {}
    real_run_git = run_git

    def spy(args, **kwargs):
        if "clone" in args:
            captured["timeout"] = kwargs.get("timeout")
        return real_run_git(args, **kwargs)

    monkeypatch.setattr("apps.churn.clones.run_git", spy)

    ensure_clone(repository, credentials=None, timeout=17)

    assert captured["timeout"] == 17


def test_second_consecutive_failure_raises(origin_remote, monkeypatch, tmp_path):
    repository = RepositoryFactory(full_name="acme/widgets")
    ensure_clone(repository, credentials=None)

    def always_fails(args, **kwargs):
        raise GitOperationError("simulated failure", reason="git_error")

    monkeypatch.setattr("apps.churn.clones.run_git", always_fails)

    with pytest.raises(GitOperationError):
        ensure_clone(repository, credentials=None)
