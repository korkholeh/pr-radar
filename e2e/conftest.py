"""e2e fixtures. The suite attaches to a surface the orchestrator already started
(`make e2e-up`); it never starts a server itself. See e2e/surfaces.toml and e2e/README.md."""

import json
import os
import socket
import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlparse

import pytest

SURFACES = tomllib.loads((Path(__file__).parent / "surfaces.toml").read_text())
REPO_ROOT = Path(__file__).parent.parent


def _resolve_base_url() -> str:
    app = SURFACES["app"]
    return os.environ.get(app["base_url_env"], app["default_base_url"])


def _surface_up(base_url: str) -> bool:
    parsed = urlparse(base_url)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def e2e_base_url() -> str:
    resolved = _resolve_base_url()
    if not _surface_up(resolved):
        pytest.fail(
            f"e2e surface at {resolved} is not reachable. Start it with `{SURFACES['app']['start_command']}` "
            f"first, and stop it afterwards with `{SURFACES['app']['stop_command']}`."
        )
    return resolved


@pytest.fixture(scope="session")
def base_url(e2e_base_url: str) -> str:
    """Overrides pytest-playwright's own `base_url` fixture with the resolved surface address."""
    return e2e_base_url


@pytest.fixture(scope="session", autouse=True)
def seed_ids(e2e_base_url: str) -> dict:
    """`make e2e-up` seeds once when it starts the surface, but the orchestrator is allowed to
    re-run `pytest e2e` against that same, still-running surface without tearing it down and
    re-upping in between. `manage.py seed_e2e` is written to be idempotent against its own past
    UI-driven mutations (see its docstring), so re-running it here -- a plain data reset, no
    server/worker process touched -- keeps every spec's fixture rows (unmapped identities, the
    merge-source person, etc.) in their pristine starting state regardless of how many times this
    session runs against an unrestarted surface.

    Also returns whatever row ids the seed command printed on its `E2E_SEED_IDS=` line (a phase
    whose feature has no page yet that links to a seeded row -- e.g. ai_detection's PR detail page
    before phase 9 ships a PR list -- reads the pk from here instead of querying the database
    behind the app's back)."""
    del e2e_base_url  # ensures the surface is reachable before we touch its database
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.e2e"}
    result = subprocess.run(
        [sys.executable, "manage.py", "seed_e2e"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("E2E_SEED_IDS="):
            return json.loads(line.removeprefix("E2E_SEED_IDS="))
    return {}


@pytest.fixture(scope="session")
def duration_cases() -> list[dict]:
    path = Path(__file__).parent.parent / "tests" / "fixtures" / "duration_cases.json"
    return json.loads(path.read_text())
