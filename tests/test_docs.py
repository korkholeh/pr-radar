"""A new connection-check code, or a new derived PR field, must ship documented — see CLAUDE.md's docs
convention."""

import dataclasses
from pathlib import Path

from apps.activity.derive import DerivedFields, FileDerivedFields
from apps.connections.check_codes import CHECK_CODES

DOCS_ROOT = Path(__file__).resolve().parent.parent / "docs"
GITHUB_CONNECTIONS_DOCS_PATH = DOCS_ROOT / "GITHUB_CONNECTIONS.md"
DECISIONS_DOCS_PATH = DOCS_ROOT / "DECISIONS.md"

# Fields that are bookkeeping (row identity, internal linkage) rather than a metric-facing rule and so
# aren't expected to appear in the prose definition table.
DERIVED_FIELD_DOC_EXEMPTIONS = {"pk", "files", "reverts_pr_id"}


def test_every_check_code_is_documented():
    text = GITHUB_CONNECTIONS_DOCS_PATH.read_text(encoding="utf-8")
    missing = [code for code in CHECK_CODES if f"`{code}`" not in text]
    assert not missing, f"docs/GITHUB_CONNECTIONS.md is missing check code(s): {missing}"


def test_every_derived_field_is_documented():
    field_names = {
        field.name
        for dataclass_type in (DerivedFields, FileDerivedFields)
        for field in dataclasses.fields(dataclass_type)
    } - DERIVED_FIELD_DOC_EXEMPTIONS
    text = DECISIONS_DOCS_PATH.read_text(encoding="utf-8")
    missing = [name for name in sorted(field_names) if f"`{name}`" not in text]
    assert not missing, f"docs/DECISIONS.md is missing derived field(s): {missing}"
