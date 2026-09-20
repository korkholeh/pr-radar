"""The shared pull-request-body section parser (phase 12, stage 7).

The heading and boundary machinery came out of `disclosure.py` unchanged — `test_disclosure.py`
still covers it through the disclosure parser, and what is tested here is the two helpers the
policy engine added on top: reading a section's prose, and deciding whether it carries real content.

"Real content" is where the judgement lives. A policy check that accepted a ticked-but-empty
checkbox, a lone bullet, or the template's own `<!-- instructions -->` would be satisfied by an
untouched template, which is exactly the failure the check exists to catch.
"""

from apps.ai_detection.body_sections import (
    find_section,
    has_section_content,
    heading_candidate,
    normalize_heading,
    section_text,
)

RISK = ("Risk level", "Risk")
VERIFICATION = ("Verification", "How verified")


def test_section_text_reads_the_prose_under_an_atx_heading():
    body = "## Risk level\n\nMedium — reporting query only.\n\n## Verification\n\nRan the suite.\n"
    assert section_text(body, RISK) == "Medium — reporting query only."
    assert section_text(body, VERIFICATION) == "Ran the suite."


def test_section_text_is_none_when_the_heading_is_absent():
    """Absent and empty are different answers, and a caller needs to tell them apart."""
    assert section_text("Just a description.", RISK) is None
    assert section_text("## Risk level\n\n", RISK) == ""


def test_section_text_accepts_the_heading_styles_a_person_actually_writes():
    for body in (
        "## Risk level\n\nLow.\n",
        "**Risk level**\n\nLow.\n",
        "Risk level\n----------\n\nLow.\n",
        "- Risk level\n\nLow.\n",
    ):
        assert section_text(body, RISK) == "Low.", body


def test_section_text_matches_a_heading_case_insensitively():
    assert section_text("## RISK LEVEL\n\nLow.\n", RISK) == "Low."


def test_section_text_strips_the_templates_own_instructions():
    """A template's guidance lives in HTML comments, so an unfilled section must read as empty."""
    body = "## Risk level\n\n<!-- low / medium / high -->\n"
    assert section_text(body, RISK) == ""


def test_section_text_stops_at_the_next_heading():
    body = "## Risk level\n\nLow.\n\n## Verification\n\nRan the suite.\n"
    assert "Verification" not in (section_text(body, RISK) or "")


def test_has_section_content_is_true_for_a_filled_section():
    assert has_section_content("## Risk level\n\nMedium.\n", RISK) is True


def test_has_section_content_is_false_for_an_absent_heading():
    assert has_section_content("Nothing here.", RISK) is False


def test_has_section_content_is_false_for_an_untouched_template():
    for body in (
        "## Risk level\n\n<!-- state the level -->\n",
        "## Risk level\n\n-\n",
        "## Risk level\n\n[ ]\n",
        "## Risk level\n\n[x]\n",
        "## Risk level\n",
    ):
        assert has_section_content(body, RISK) is False, body


def test_n_a_counts_as_an_answer():
    """Somebody read the section and answered it. Treating that as "said nothing" would accuse a
    developer who did the thing the template asked for."""
    assert has_section_content("## Risk level\n\nN/A\n", RISK) is True


def test_find_section_and_heading_candidate_are_still_exported():
    """`disclosure.py` imports these rather than keeping its own copy; a rename would break the
    disclosure parser silently, since both call sites are dynamic aliases."""
    lines = ["## Risk level", "", "Low."]
    assert heading_candidate(lines, 0) == ("Risk level", 1)
    assert find_section(lines, RISK) == (1, 3)
    assert normalize_heading("## Risk level ") == "risk level"
