import subprocess
import sys
from pathlib import Path

ASKPASS_PATH = Path(__file__).resolve().parent.parent / "askpass.py"


def _run(prompt: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ASKPASS_PATH), prompt],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )


def test_is_executable_with_a_shebang():
    assert ASKPASS_PATH.stat().st_mode & 0o111
    first_line = ASKPASS_PATH.read_text().splitlines()[0]
    assert first_line.startswith("#!")


def test_username_prompt_prints_the_username():
    result = _run("Username for 'https://github.com':", {"PR_RADAR_GIT_USERNAME": "x-access-token"})
    assert result.returncode == 0
    assert result.stdout == "x-access-token"


def test_password_prompt_prints_the_token():
    result = _run(
        "Password for 'https://x-access-token@github.com':", {"PR_RADAR_GIT_TOKEN": "ghp_secrettoken1234"}
    )
    assert result.returncode == 0
    assert result.stdout == "ghp_secrettoken1234"


def test_no_env_vars_set_prints_empty_string_and_exits_zero():
    result = _run("Password for 'https://github.com':", {})
    assert result.returncode == 0
    assert result.stdout == ""
