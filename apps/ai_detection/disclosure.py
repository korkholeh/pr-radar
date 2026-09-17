"""The tolerant PR-template disclosure parser (spec §6.2). Pure: it never touches the database
beyond the settings it is handed via `DisclosureConfig`, so it is testable without a DB write."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from apps.activity.models import AIDisclosure
from apps.catalog.services import get_dict, get_list

TOOL_RAW_MAX_LENGTH = 40
MAX_TOOLS = 10

_HEADING_STRIP_CHARS = "#*_- \t"

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_ATX_HEADING_RE = re.compile(r"^\s*#{1,6}\s*(.+?)\s*$")
_BOLD_HEADING_RE = re.compile(r"^\s*\*\*(.+?)\*\*\s*$")
_LOOSE_HEADING_RE = re.compile(r"^\s*[#*\-]+\s*(.+?)\s*$")
_HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_SETEXT_UNDERLINE_RE = re.compile(r"^\s*(?:=+|-+)\s*$")
_CHECKBOX_RE = re.compile(r"^\s*[-*]?\s*\[( |x|X)\]\s*(.+?)\s*$")
_TOOL_SPLIT_RE = re.compile(r",|/|\band\b", re.IGNORECASE)


@dataclass(frozen=True)
class DisclosureConfig:
    headings: tuple[str, ...]
    none_labels: tuple[str, ...]
    partial_labels: tuple[str, ...]
    substantial_labels: tuple[str, ...]
    tools_labels: tuple[str, ...]
    tool_aliases: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class DisclosureResult:
    disclosure: str  # an AIDisclosure value
    tools: tuple[str, ...]  # canonical Tool values, else the raw text lowercased


def load_config() -> DisclosureConfig:
    return DisclosureConfig(
        headings=tuple(get_list("DISCLOSURE_SECTION_HEADINGS")),
        none_labels=tuple(get_list("DISCLOSURE_LABELS_NONE")),
        partial_labels=tuple(get_list("DISCLOSURE_LABELS_PARTIAL")),
        substantial_labels=tuple(get_list("DISCLOSURE_LABELS_SUBSTANTIAL")),
        tools_labels=tuple(get_list("DISCLOSURE_TOOLS_LABELS")),
        tool_aliases={key: tuple(values) for key, values in get_dict("DISCLOSURE_TOOL_ALIASES").items()},
    )


def _normalize_heading(text: str) -> str:
    return text.strip(_HEADING_STRIP_CHARS).casefold()


def _is_boundary_line(line: str, next_line: str | None) -> bool:
    """True if `line` opens a new section (any heading, of any style) or is a rule — either way
    the previous section ends here. A checkbox/list-item line is never a boundary."""
    if _CHECKBOX_RE.match(line):
        return False
    if _is_hard_boundary_line(line, next_line):
        return True
    if _BOLD_HEADING_RE.match(line):
        return True
    if _HR_RE.match(line):
        return True
    return False


def _is_hard_boundary_line(line: str, next_line: str | None) -> bool:
    """ATX and setext headings always end a section, even before the first checkbox has been
    seen — unlike a bold lead-in or a horizontal rule, which are also legitimate instructional
    text placed *above* the checkboxes (see `_find_section`)."""
    if _CHECKBOX_RE.match(line):
        return False
    if _ATX_HEADING_RE.match(line):
        return True
    return bool(line.strip()) and next_line is not None and bool(_SETEXT_UNDERLINE_RE.match(next_line))


def _is_soft_boundary_line(line: str) -> bool:
    """A bold-only line or a horizontal rule: a boundary only once the section has already
    yielded a checkbox, so an instruction line (`**Tick exactly one:**`) or a `---` divider
    placed *above* the checkboxes does not truncate the section to zero lines."""
    if _CHECKBOX_RE.match(line):
        return False
    return bool(_BOLD_HEADING_RE.match(line)) or bool(_HR_RE.match(line))


def _heading_candidate(lines: list[str], index: int) -> tuple[str, int] | None:
    """If `lines[index]` looks like a heading (ATX, bold, setext or a loose leading marker),
    return its text and the index of the first content line after it."""
    line = lines[index]
    if _CHECKBOX_RE.match(line):
        return None
    atx = _ATX_HEADING_RE.match(line)
    if atx is not None:
        return atx.group(1), index + 1
    bold = _BOLD_HEADING_RE.match(line)
    if bold is not None:
        return bold.group(1), index + 1
    if line.strip() and index + 1 < len(lines) and _SETEXT_UNDERLINE_RE.match(lines[index + 1]):
        return line.strip(), index + 2
    loose = _LOOSE_HEADING_RE.match(line)
    if loose is not None:
        return loose.group(1), index + 1
    return None


def _find_section(lines: list[str], headings: tuple[str, ...]) -> tuple[int, int] | None:
    normalized = {_normalize_heading(heading) for heading in headings}
    content_start = None
    for index in range(len(lines)):
        candidate = _heading_candidate(lines, index)
        if candidate is None:
            continue
        text, after_index = candidate
        if _normalize_heading(text) in normalized:
            content_start = after_index
            break
    if content_start is None:
        return None
    end = len(lines)
    seen_checkbox = False
    for index in range(content_start, len(lines)):
        line = lines[index]
        if _CHECKBOX_RE.match(line):
            seen_checkbox = True
            continue
        next_line = lines[index + 1] if index + 1 < len(lines) else None
        if _is_hard_boundary_line(line, next_line):
            end = index
            break
        if seen_checkbox and _is_soft_boundary_line(line):
            end = index
            break
    return content_start, end


def _match_category(label_text: str, config: DisclosureConfig) -> str | None:
    folded = label_text.strip().casefold()
    for candidates, category in (
        (config.none_labels, AIDisclosure.NONE),
        (config.partial_labels, AIDisclosure.PARTIAL),
        (config.substantial_labels, AIDisclosure.SUBSTANTIAL),
    ):
        for candidate in candidates:
            if folded.startswith(candidate.strip().casefold()):
                return category
    return None


def _resolve_disclosure(lines: list[str], config: DisclosureConfig) -> str:
    section = _find_section(lines, config.headings)
    if section is None:
        return AIDisclosure.MISSING

    start, end = section
    ticked_labels = [
        match.group(2)
        for line in lines[start:end]
        if (match := _CHECKBOX_RE.match(line)) is not None and match.group(1) in ("x", "X")
    ]
    if len(ticked_labels) == 0:
        return AIDisclosure.MISSING
    if len(ticked_labels) >= 2:
        return AIDisclosure.AMBIGUOUS

    category = _match_category(ticked_labels[0], config)
    return category if category is not None else AIDisclosure.MISSING


def _map_tool(raw: str, tool_aliases: Mapping[str, tuple[str, ...]]) -> str:
    folded = raw.strip().casefold()
    for canonical, aliases in tool_aliases.items():
        if any(folded == alias.strip().casefold() for alias in aliases):
            return canonical
    return folded[:TOOL_RAW_MAX_LENGTH]


def _parse_tool_list(text: str, config: DisclosureConfig) -> tuple[str, ...]:
    stripped = _HTML_COMMENT_RE.sub("", text).strip()
    if not stripped:
        return ()
    parts = [part.strip() for part in _TOOL_SPLIT_RE.split(stripped)]
    tools = [_map_tool(part, config.tool_aliases) for part in parts if part]
    return tuple(tools[:MAX_TOOLS])


def _bare_line(line: str) -> str:
    match = _LOOSE_HEADING_RE.match(line)
    return match.group(1) if match is not None else line.strip()


def _extract_tools(lines: list[str], config: DisclosureConfig) -> tuple[str, ...]:
    for index, line in enumerate(lines):
        bare = _bare_line(line)
        for label in config.tools_labels:
            if bare.casefold().startswith(label.casefold()):
                remainder = bare[len(label) :].lstrip(" :\t")
                if not remainder:
                    for later_index in range(index + 1, len(lines)):
                        later = lines[later_index]
                        later_next = lines[later_index + 1] if later_index + 1 < len(lines) else None
                        if _is_boundary_line(later, later_next):
                            break
                        later_stripped = later.strip()
                        if later_stripped:
                            remainder = later_stripped
                            break
                return _parse_tool_list(remainder, config)
    return ()


def parse_disclosure(body: str, config: DisclosureConfig) -> DisclosureResult:
    lines = (body or "").splitlines()
    return DisclosureResult(
        disclosure=_resolve_disclosure(lines, config),
        tools=_extract_tools(lines, config),
    )
