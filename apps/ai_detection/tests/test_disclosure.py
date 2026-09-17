import pytest

from apps.activity.models import AIDisclosure
from apps.ai_detection.disclosure import DisclosureConfig, load_config, parse_disclosure

_DEFAULT_CONFIG = DisclosureConfig(
    headings=("AI assistance",),
    none_labels=("None",),
    partial_labels=("Partial",),
    substantial_labels=("Substantial",),
    tools_labels=("AI tools used",),
    tool_aliases={
        "claude_code": ("claude code", "claude"),
        "copilot": ("copilot", "github copilot"),
        "cursor": ("cursor",),
        "codex": ("codex",),
        "devin": ("devin",),
        "gemini": ("gemini",),
        "aider": ("aider",),
        "windsurf": ("windsurf",),
        "chatgpt": ("chatgpt", "gpt"),
    },
)


def test_lowercase_tick_partial():
    body = (
        "### AI assistance\n- [ ] None\n- [x] Partial (autocomplete, snippets, review)\n- [ ] Substantial\n"
    )
    result = parse_disclosure(body, _DEFAULT_CONFIG)
    assert result.disclosure == AIDisclosure.PARTIAL


def test_uppercase_tick_substantial():
    body = "### AI assistance\n- [ ] None\n- [ ] Partial\n- [X] Substantial (most of the change)\n"
    result = parse_disclosure(body, _DEFAULT_CONFIG)
    assert result.disclosure == AIDisclosure.SUBSTANTIAL


def test_none_category():
    body = "### AI assistance\n- [x] None\n- [ ] Partial\n- [ ] Substantial\n"
    result = parse_disclosure(body, _DEFAULT_CONFIG)
    assert result.disclosure == AIDisclosure.NONE


def test_mixed_case_heading_is_found():
    body = "### ai ASSISTANCE\n- [x] Substantial\n"
    result = parse_disclosure(body, _DEFAULT_CONFIG)
    assert result.disclosure == AIDisclosure.SUBSTANTIAL


def test_malformed_template_is_missing():
    body = "### AI assistance\n- [x] Something unexpected\n"
    result = parse_disclosure(body, _DEFAULT_CONFIG)
    assert result.disclosure == AIDisclosure.MISSING


def test_two_ticked_boxes_are_ambiguous():
    body = "### AI assistance\n- [x] None\n- [x] Partial\n- [ ] Substantial\n"
    result = parse_disclosure(body, _DEFAULT_CONFIG)
    assert result.disclosure == AIDisclosure.AMBIGUOUS


def test_missing_section_is_missing():
    body = "Just a regular PR description with no AI section at all."
    result = parse_disclosure(body, _DEFAULT_CONFIG)
    assert result.disclosure == AIDisclosure.MISSING


def test_empty_body_is_missing():
    result = parse_disclosure("", _DEFAULT_CONFIG)
    assert result.disclosure == AIDisclosure.MISSING
    assert result.tools == ()


def test_tools_line_is_parsed_into_canonical_values():
    body = "### AI assistance\n- [x] Partial\n\n### AI tools used\nClaude Code, Copilot\n"
    result = parse_disclosure(body, _DEFAULT_CONFIG)
    assert result.tools == ("claude_code", "copilot")


def test_bare_placeholder_comment_yields_no_tools():
    body = (
        "### AI assistance\n- [x] Partial\n\n### AI tools used\n<!-- e.g. Claude Code, Copilot, Cursor -->\n"
    )
    result = parse_disclosure(body, _DEFAULT_CONFIG)
    assert result.tools == ()


def test_unknown_tool_kept_as_raw_text():
    body = "### AI assistance\n- [x] Partial\n\n### AI tools used\nWindsurf, SomeNewTool\n"
    result = parse_disclosure(body, _DEFAULT_CONFIG)
    assert result.tools == ("windsurf", "somenewtool")


def test_section_ends_at_bold_heading_not_just_atx():
    body = "### AI assistance\n- [x] None\n\n**Checklist**\n\n- [x] Tests added\n- [x] Docs updated\n"
    result = parse_disclosure(body, _DEFAULT_CONFIG)
    assert result.disclosure == AIDisclosure.NONE


def test_section_ends_at_horizontal_rule():
    body = "### AI assistance\n- [x] None\n\n---\n\n- [x] I ran the tests\n"
    result = parse_disclosure(body, _DEFAULT_CONFIG)
    assert result.disclosure == AIDisclosure.NONE


def test_section_ends_at_setext_heading():
    body = "### AI assistance\n- [x] None\n\nNext section\n------------\n- [x] Something else\n"
    result = parse_disclosure(body, _DEFAULT_CONFIG)
    assert result.disclosure == AIDisclosure.NONE


def test_bold_lead_in_before_checkboxes_does_not_truncate_the_section():
    body = "### AI assistance\n\n**Tick exactly one:**\n\n- [x] Partial\n"
    result = parse_disclosure(body, _DEFAULT_CONFIG)
    assert result.disclosure == AIDisclosure.PARTIAL


def test_horizontal_rule_before_checkboxes_does_not_truncate_the_section():
    body = "### AI assistance\n\n---\n\n- [x] Partial\n"
    result = parse_disclosure(body, _DEFAULT_CONFIG)
    assert result.disclosure == AIDisclosure.PARTIAL


def test_bold_lead_in_without_blank_line_before_checkboxes():
    body = "### AI assistance\n**Please choose one**\n- [x] Substantial\n"
    result = parse_disclosure(body, _DEFAULT_CONFIG)
    assert result.disclosure == AIDisclosure.SUBSTANTIAL


def test_bold_section_heading_is_found():
    body = "**AI assistance**\n- [x] Partial\n"
    result = parse_disclosure(body, _DEFAULT_CONFIG)
    assert result.disclosure == AIDisclosure.PARTIAL


def test_setext_section_heading_is_found():
    body = "AI assistance\n-------------\n- [x] Partial\n"
    result = parse_disclosure(body, _DEFAULT_CONFIG)
    assert result.disclosure == AIDisclosure.PARTIAL


def test_tools_fallback_does_not_swallow_the_next_heading():
    body = "### AI assistance\n- [x] Partial\n\n### AI tools used\n\n### Checklist\n- [ ] Tests\n"
    result = parse_disclosure(body, _DEFAULT_CONFIG)
    assert result.tools == ()


def test_tools_line_outside_any_section_still_parses():
    """Intentional: the tools label is searched over the whole body, not just the AI-assistance
    section, because the shipped `docs/pull_request_template.md` puts "AI tools used" in its own
    ATX section outside it — an in-section-only scan would find no tools for that template."""
    body = "## Description\nAI tools used: Copilot\n\nNothing else.\n"
    result = parse_disclosure(body, _DEFAULT_CONFIG)
    assert result.disclosure == AIDisclosure.MISSING
    assert result.tools == ("copilot",)


def test_configurable_ukrainian_heading_is_found():
    config = DisclosureConfig(
        headings=("AI assistance", "ШІ допомога"),
        none_labels=_DEFAULT_CONFIG.none_labels,
        partial_labels=_DEFAULT_CONFIG.partial_labels,
        substantial_labels=_DEFAULT_CONFIG.substantial_labels,
        tools_labels=_DEFAULT_CONFIG.tools_labels,
        tool_aliases=_DEFAULT_CONFIG.tool_aliases,
    )
    body = "### ШІ допомога\n- [x] Partial\n"
    result = parse_disclosure(body, config)
    assert result.disclosure == AIDisclosure.PARTIAL


@pytest.mark.django_db
def test_load_config_builds_from_app_settings():
    config = load_config()
    assert config.headings == ("AI assistance",)
    assert config.tools_labels == ("AI tools used",)
    assert config.tool_aliases["claude_code"] == ("claude code", "claude")
