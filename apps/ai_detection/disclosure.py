"""The tolerant PR-template disclosure parser (spec §6.2). Pure: it never touches the database
beyond the settings it is handed via `DisclosureConfig`, so it is testable without a DB write.

The heading and boundary machinery moved to `body_sections.py` in phase 12 stage 7, unchanged, so
the policy engine's body-section checks (is a risk level stated, is a task linked) read a PR
template exactly the way this parser does instead of growing a second, drifting definition of what
a heading is. The private aliases below keep this module's own code reading as it did."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from apps.activity.models import AIDisclosure
from apps.ai_detection import body_sections
from apps.catalog.services import get_dict, get_list

TOOL_RAW_MAX_LENGTH = 40
MAX_TOOLS = 10

_HTML_COMMENT_RE = body_sections.HTML_COMMENT_RE
_CHECKBOX_RE = body_sections.CHECKBOX_RE
_LOOSE_HEADING_RE = body_sections.LOOSE_HEADING_RE
_TOOL_SPLIT_RE = re.compile(r",|/|\band\b", re.IGNORECASE)

_find_section = body_sections.find_section
_is_boundary_line = body_sections.is_boundary_line


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
