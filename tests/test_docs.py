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
from apps.catalog.setting_defs import SETTING_DEFS
from apps.connections.check_codes import CHECK_CODES
from apps.metrics.registry import REGISTRY
from apps.policy.models import PolicyViolation, SensitivePathRule
from apps.policy.rules import SEVERITY

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_ROOT = REPO_ROOT / "docs"
GITHUB_CONNECTIONS_DOCS_PATH = DOCS_ROOT / "GITHUB_CONNECTIONS.md"
DECISIONS_DOCS_PATH = DOCS_ROOT / "DECISIONS.md"
PR_TEMPLATE_PATH = DOCS_ROOT / "pull_request_template.md"
POLICY_DOCS_PATH = DOCS_ROOT / "POLICY.md"
CONFIGURATION_DOCS_PATH = DOCS_ROOT / "CONFIGURATION.md"
METRICS_DOCS_PATH = DOCS_ROOT / "METRICS.md"
README_PATH = REPO_ROOT / "README.md"
CLAUDE_MD_PATH = REPO_ROOT / "CLAUDE.md"

# `docs/[A-Za-z0-9_./-]+` also swallows trailing prose punctuation (a sentence-final period, a
# comma, a closing paren around the reference, a closing backtick) since `.` is a literal in the
# character class -- strip it before resolving the path on disk.
DOCS_PATH_RE = re.compile(r"docs/[A-Za-z0-9_./-]+")
_TRAILING_PROSE_PUNCTUATION = ".,)`"

# `[text](relative/path.md)` or `[text](relative/path.md#anchor)`; deliberately excludes `![...]`
# image syntax (not used under docs/) and anything with `://` or `mailto:` in the target.
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")

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


def test_every_rule_code_status_and_ai_mode_is_documented():
    text = POLICY_DOCS_PATH.read_text(encoding="utf-8")
    values = (
        *PolicyViolation.RuleCode.values,
        *PolicyViolation.Status.values,
        *SensitivePathRule.AiMode.values,
    )
    missing = [value for value in values if f"`{value}`" not in text]
    assert not missing, f"docs/POLICY.md is missing value(s): {missing}"


def test_policy_severity_table_matches_the_code():
    text = POLICY_DOCS_PATH.read_text(encoding="utf-8")
    rows = re.findall(r"^\| `([A-Z_]+)` \| (\w+) \|", text, re.MULTILINE)
    documented = {code: severity for code, severity in rows if code in PolicyViolation.RuleCode.values}
    assert documented == dict(SEVERITY)


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


def test_every_setting_is_documented():
    text = CONFIGURATION_DOCS_PATH.read_text(encoding="utf-8")
    missing = [setting_def.key for setting_def in SETTING_DEFS if f"`{setting_def.key}`" not in text]
    assert not missing, f"docs/CONFIGURATION.md is missing setting(s): {missing}"


def test_every_metric_key_is_documented():
    text = METRICS_DOCS_PATH.read_text(encoding="utf-8")
    missing = [key for key in REGISTRY if f"`{key}`" not in text]
    assert not missing, f"docs/METRICS.md is missing metric key(s): {missing}"


def test_every_docs_path_referenced_by_readme_and_claude_md_exists():
    missing = []
    for source in (README_PATH, CLAUDE_MD_PATH):
        text = source.read_text(encoding="utf-8")
        for raw_match in DOCS_PATH_RE.findall(text):
            candidate = raw_match.rstrip(_TRAILING_PROSE_PUNCTUATION)
            target = REPO_ROOT / candidate
            # No extension on the last path segment, or an explicit trailing slash, means the
            # prose is pointing at a directory (e.g. `docs/user/`, `docs/dev/adr/`).
            looks_like_directory = candidate.endswith("/") or "." not in target.name
            ok = target.is_dir() if looks_like_directory else target.is_file()
            if not ok:
                missing.append(f"{source.name}: {candidate}")
    assert not missing, f"referenced docs path(s) do not exist: {missing}"


def test_every_docs_internal_link_resolves():
    broken = []
    for doc_path in sorted(DOCS_ROOT.rglob("*.md")):
        text = doc_path.read_text(encoding="utf-8")
        for target in MARKDOWN_LINK_RE.findall(text):
            target = target.strip()
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            relative_target = target.split("#", 1)[0]
            if not relative_target:
                continue
            resolved = (doc_path.parent / relative_target).resolve()
            if not resolved.is_file():
                broken.append(f"{doc_path.relative_to(REPO_ROOT)}: {target}")
    assert not broken, f"broken internal doc link(s): {broken}"
