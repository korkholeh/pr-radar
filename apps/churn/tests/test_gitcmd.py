import subprocess

import pytest

from apps.churn.gitcmd import ASKPASS_PATH, GitOperationError, git_env, run_git


def test_git_env_with_credentials_has_askpass_and_both_vars():
    env = git_env(("x-access-token", "ghp_realtoken1234"))
    assert env["GIT_ASKPASS"] == str(ASKPASS_PATH)
    assert env["PR_RADAR_GIT_USERNAME"] == "x-access-token"
    assert env["PR_RADAR_GIT_TOKEN"] == "ghp_realtoken1234"
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_git_env_without_credentials_has_no_token_vars():
    env = git_env(None)
    assert "GIT_ASKPASS" not in env
    assert "PR_RADAR_GIT_USERNAME" not in env
    assert "PR_RADAR_GIT_TOKEN" not in env


def test_run_git_never_puts_credentials_in_args(monkeypatch):
    captured = {}

    def _fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    run_git(["status"], credentials=("x-access-token", "ghp_realtoken1234"))

    assert captured["argv"] == ["git", "status"]
    assert all("ghp_realtoken1234" not in arg for arg in captured["argv"])
    assert captured["env"]["PR_RADAR_GIT_TOKEN"] == "ghp_realtoken1234"


def test_run_git_returns_stdout(monkeypatch):
    def _fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="output\n", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    assert run_git(["status"]) == "output\n"


def test_run_git_non_zero_exit_raises_masked_error(monkeypatch):
    def _fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 128, stdout="", stderr="fatal: auth failed for ghp_realtoken1234abcd"
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)

    with pytest.raises(GitOperationError) as exc_info:
        run_git(["fetch"])

    assert "ghp_realtoken1234abcd" not in str(exc_info.value)
    assert "abcd" in str(exc_info.value)  # last 4 chars survive masking
    assert exc_info.value.reason == "git_error"


def test_run_git_masks_the_exact_credential_even_without_a_known_token_prefix(monkeypatch):
    """`mask_secrets` only matches `ghp_`/`gho_`/... shapes; `run_git` also masks the literal
    credential it was called with, so a token of any shape never reaches `GitOperationError`."""

    def _fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 128, stdout="", stderr="fatal: auth failed for custom_secret_value_1234"
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)

    with pytest.raises(GitOperationError) as exc_info:
        run_git(["fetch"], credentials=("x-access-token", "custom_secret_value_1234"))

    assert "custom_secret_value_1234" not in str(exc_info.value)
    assert "1234" in str(exc_info.value)


def test_run_git_timeout_raises_with_reason_timeout(monkeypatch):
    def _fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout") or 1)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    with pytest.raises(GitOperationError) as exc_info:
        run_git(["fetch"], timeout=1)

    assert exc_info.value.reason == "timeout"


def test_run_git_against_a_real_repository(tmp_path):
    run_git(["init", "--quiet", str(tmp_path)])
    run_git(
        [
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-m",
            "initial",
        ]
    )
    output = run_git(["-C", str(tmp_path), "rev-list", "-n", "1", "HEAD"])
    assert len(output.strip()) == 40
