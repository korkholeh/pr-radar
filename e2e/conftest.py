"""e2e fixtures. The suite attaches to a surface the orchestrator already started
(`make e2e-up`); it never starts a server itself. See e2e/surfaces.toml and e2e/README.md."""

import json
import os
import socket
import tomllib
from pathlib import Path
from urllib.parse import urlparse

import pytest

SURFACES = tomllib.loads((Path(__file__).parent / "surfaces.toml").read_text())


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


@pytest.fixture(scope="session")
def duration_cases() -> list[dict]:
    path = Path(__file__).parent.parent / "tests" / "fixtures" / "duration_cases.json"
    return json.loads(path.read_text())
