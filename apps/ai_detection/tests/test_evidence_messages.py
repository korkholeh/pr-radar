"""`evidence.py` — turning a stored `(evidence_code, evidence_params)` into a sentence, and the
bilingual rule that makes the indirection necessary (phase 12, stage 4)."""

import re
from pathlib import Path

import pytest
from django.utils import translation

from apps.ai_detection.evidence import EVIDENCE_MESSAGES, render_evidence
from apps.ai_detection.models import SignalKind

PO_PATH = Path(__file__).resolve().parents[3] / "locale" / "uk" / "LC_MESSAGES" / "django.po"


def test_every_kind_has_an_evidence_message():
    assert set(EVIDENCE_MESSAGES) == set(SignalKind.values)


def test_a_message_states_the_threshold_as_well_as_the_measurement():
    """A lead reading "0.7 hours" cannot judge it without knowing the rule said two."""
    rendered = render_evidence(
        "fast_large_pr",
        {"lines": 600, "files": 12, "hours": 0.7, "min_lines": 400, "min_files": 8, "max_hours": 2},
    )
    assert "600" in rendered
    assert "0.7" in rendered
    assert "400" in rendered and "2" in rendered


def test_an_unknown_code_renders_the_code_rather_than_raising():
    """A row written by a future version of a kind must never 500 the pull-request page."""
    assert render_evidence("from_the_future", {"x": 1}) == "from_the_future"


def test_a_missing_parameter_renders_a_question_mark_rather_than_raising():
    rendered = render_evidence("fast_large_pr", {"lines": 600})
    assert "?" in rendered
    assert "600" in rendered


def test_a_count_bearing_message_uses_the_plural_form():
    one = render_evidence(
        "instant_review_response", {"occurrences": 1, "max_minutes": 5, "min_occurrences": 3}
    )
    many = render_evidence(
        "instant_review_response", {"occurrences": 4, "max_minutes": 5, "min_occurrences": 3}
    )
    assert one != many


def _po_entries() -> dict[str, str]:
    """msgid -> msgstr, with continuation lines joined. A hand-rolled reader rather than polib:
    the project has no such dependency and this only needs the two fields."""
    text = PO_PATH.read_text(encoding="utf-8")
    entries: dict[str, str] = {}
    current_id: list[str] = []
    current_str: list[str] = []
    target: list[str] | None = None
    fuzzy = False
    fuzzy_ids: set[str] = set()

    def flush():
        if current_id:
            msgid = "".join(current_id)
            entries[msgid] = "".join(current_str)
            if fuzzy:
                fuzzy_ids.add(msgid)

    for line in text.splitlines():
        if line.startswith("#, ") and "fuzzy" in line:
            fuzzy = True
            continue
        if line.startswith("msgid "):
            flush()
            current_id, current_str = [_po_value(line)], []
            target = current_id
            continue
        if line.startswith("msgstr "):
            current_str = [_po_value(line)]
            target = current_str
            continue
        if line.startswith('"') and target is not None:
            target.append(_po_value(line))
            continue
        if not line.strip():
            flush()
            current_id, current_str, target, fuzzy = [], [], None, False
    flush()
    entries["__fuzzy__"] = "\n".join(sorted(fuzzy_ids))
    return entries


def _po_value(line: str) -> str:
    match = re.search(r'"(.*)"\s*$', line)
    return match.group(1) if match else ""


@pytest.mark.parametrize("code", sorted(EVIDENCE_MESSAGES))
def test_every_evidence_sentence_is_translated_into_ukrainian_and_not_fuzzy(code):
    """CLAUDE.md: a new UI string gets its Ukrainian translation in the same phase. These
    sentences are the reason `evidence_code` exists at all — storing the English would make them
    untranslatable forever."""
    entries = _po_entries()
    fuzzy = set(entries["__fuzzy__"].splitlines())
    entry = EVIDENCE_MESSAGES[code]

    for key in ("singular", "plural"):
        source = entry.get(key)
        if not source:
            continue
        # The .po file escapes what Python does not; compare on the escaped form gettext wrote.
        msgid = source.replace("\\", "\\\\").replace('"', '\\"')
        assert msgid in entries, f"{code}: {key!r} is missing from django.po"
        assert entries[msgid].strip(), f"{code}: {key!r} has no Ukrainian translation"
        assert msgid not in fuzzy, f"{code}: {key!r} is marked fuzzy"


def test_a_rendered_sentence_changes_with_the_active_language():
    params = {"added_files": 12, "directories": 4, "min_added_files": 10, "min_directories": 3}
    with translation.override("en"):
        english = render_evidence("mass_file_creation", params)
    with translation.override("uk"):
        ukrainian = render_evidence("mass_file_creation", params)
    assert english != ukrainian
