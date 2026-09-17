"""A new connection-check code must ship documented — see CLAUDE.md's docs convention."""

from pathlib import Path

from apps.connections.check_codes import CHECK_CODES

DOCS_PATH = Path(__file__).resolve().parent.parent / "docs" / "GITHUB_CONNECTIONS.md"


def test_every_check_code_is_documented():
    text = DOCS_PATH.read_text(encoding="utf-8")
    missing = [code for code in CHECK_CODES if f"`{code}`" not in text]
    assert not missing, f"docs/GITHUB_CONNECTIONS.md is missing check code(s): {missing}"
