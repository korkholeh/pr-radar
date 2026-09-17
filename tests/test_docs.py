"""A new connection-check code, or a new derived PR field, must ship documented — see CLAUDE.md's docs
convention."""

import dataclasses
import re
from pathlib import Path

import pytest

from apps.activity.derive import DerivedFields, FileDerivedFields
from apps.activity.models import AIDisclosure, AIStatus
from apps.ai_detection.disclosure import load_config, parse_disclosure
from apps.ai_detection.models import Detector
from apps.connections.check_codes import CHECK_CODES

DOCS_ROOT = Path(__file__).resolve().parent.parent / "docs"
GITHUB_CONNECTIONS_DOCS_PATH = DOCS_ROOT / "GITHUB_CONNECTIONS.md"
DECISIONS_DOCS_PATH = DOCS_ROOT / "DECISIONS.md"
PR_TEMPLATE_PATH = DOCS_ROOT / "pull_request_template.md"

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


def _extract_template_markdown() -> str:
    text = PR_TEMPLATE_PATH.read_text(encoding="utf-8")
    match = re.search(r"```markdown\n(.*?)```", text, re.DOTALL)
    assert match is not None, "docs/pull_request_template.md has no fenced markdown block"
    return match.group(1)


def test_every_detector_is_documented():
    text = DECISIONS_DOCS_PATH.read_text(encoding="utf-8")
    missing = [value for value in Detector.values if f"`{value}`" not in text]
    assert not missing, f"docs/DECISIONS.md is missing detector(s): {missing}"


def test_every_ai_status_and_disclosure_value_is_documented():
    text = DECISIONS_DOCS_PATH.read_text(encoding="utf-8")
    missing = [value for value in (*AIStatus.values, *AIDisclosure.values) if f"`{value}`" not in text]
    assert not missing, f"docs/DECISIONS.md is missing ai_status/ai_disclosure value(s): {missing}"


@pytest.mark.django_db
def test_pull_request_template_parses_as_the_parser_expects():
    config = load_config()
    template = _extract_template_markdown()

    all_unticked = parse_disclosure(template, config)
    assert all_unticked.disclosure == AIDisclosure.MISSING

    third_ticked = template.replace(
        "- [ ] Substantial — most of the diff was AI-generated",
        "- [x] Substantial — most of the diff was AI-generated",
    )
    assert third_ticked != template
    substantial = parse_disclosure(third_ticked, config)
    assert substantial.disclosure == AIDisclosure.SUBSTANTIAL
