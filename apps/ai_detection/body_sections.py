"""The tolerant pull-request-body section parser, shared by disclosure and policy (phase 12,
stage 7).

Every one of these helpers was written for the disclosure parser in phase 5 and is unchanged here:
`disclosure.py` imports them rather than keeping its own copy. The reason to move them is that the
body-section policy checks — is a risk level stated, is there a verification note, is a task
linked, does a high-risk change carry a plan — ask the same question of a different heading, and a
second implementation of "what counts as a heading in a PR template" would drift from the first.

Tolerant on purpose. A real PR template is written by a person: the heading may be `## AI usage`,
`**AI usage**`, `AI usage` over a row of dashes, or `- AI usage`, and the section may end at the
next heading, a horizontal rule, or the end of the body. Anything this cannot read is reported as
absent rather than guessed at, because a policy check that invents a section a reader cannot see is
worse than one that misses it.
"""

from __future__ import annotations

import re

_HEADING_STRIP_CHARS = "#*_- \t"

HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
ATX_HEADING_RE = re.compile(r"^\s*#{1,6}\s*(.+?)\s*$")
BOLD_HEADING_RE = re.compile(r"^\s*\*\*(.+?)\*\*\s*$")
LOOSE_HEADING_RE = re.compile(r"^\s*[#*\-]+\s*(.+?)\s*$")
HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
SETEXT_UNDERLINE_RE = re.compile(r"^\s*(?:=+|-+)\s*$")
CHECKBOX_RE = re.compile(r"^\s*[-*]?\s*\[( |x|X)\]\s*(.+?)\s*$")


def normalize_heading(text: str) -> str:
    return text.strip(_HEADING_STRIP_CHARS).casefold()


def is_boundary_line(line: str, next_line: str | None) -> bool:
    """True if `line` opens a new section (any heading, of any style) or is a rule — either way
    the previous section ends here. A checkbox/list-item line is never a boundary."""
    if CHECKBOX_RE.match(line):
        return False
    if is_hard_boundary_line(line, next_line):
        return True
    if BOLD_HEADING_RE.match(line):
        return True
    if HR_RE.match(line):
        return True
    return False


def is_hard_boundary_line(line: str, next_line: str | None) -> bool:
    """ATX and setext headings always end a section, even before the first checkbox has been
    seen — unlike a bold lead-in or a horizontal rule, which are also legitimate instructional
    text placed *above* the checkboxes (see `find_section`)."""
    if CHECKBOX_RE.match(line):
        return False
    if ATX_HEADING_RE.match(line):
        return True
    return bool(line.strip()) and next_line is not None and bool(SETEXT_UNDERLINE_RE.match(next_line))


def is_soft_boundary_line(line: str) -> bool:
    """A bold-only line or a horizontal rule: a boundary only once the section has already
    yielded a checkbox, so an instruction line (`**Tick exactly one:**`) or a `---` divider
    placed *above* the checkboxes does not truncate the section to zero lines."""
    if CHECKBOX_RE.match(line):
        return False
    return bool(BOLD_HEADING_RE.match(line)) or bool(HR_RE.match(line))


def heading_candidate(lines: list[str], index: int) -> tuple[str, int] | None:
    """If `lines[index]` looks like a heading (ATX, bold, setext or a loose leading marker),
    return its text and the index of the first content line after it."""
    line = lines[index]
    if CHECKBOX_RE.match(line):
        return None
    atx = ATX_HEADING_RE.match(line)
    if atx is not None:
        return atx.group(1), index + 1
    bold = BOLD_HEADING_RE.match(line)
    if bold is not None:
        return bold.group(1), index + 1
    if line.strip() and index + 1 < len(lines) and SETEXT_UNDERLINE_RE.match(lines[index + 1]):
        return line.strip(), index + 2
    loose = LOOSE_HEADING_RE.match(line)
    if loose is not None:
        return loose.group(1), index + 1
    return None


def find_section(lines: list[str], headings: tuple[str, ...]) -> tuple[int, int] | None:
    """`(first content line, one past the last)` for the first section whose heading matches any
    of `headings`, or `None` when no such heading is present."""
    normalized = {normalize_heading(heading) for heading in headings}
    content_start = None
    for index in range(len(lines)):
        candidate = heading_candidate(lines, index)
        if candidate is None:
            continue
        text, after_index = candidate
        if normalize_heading(text) in normalized:
            content_start = after_index
            break
    if content_start is None:
        return None
    end = len(lines)
    seen_checkbox = False
    for index in range(content_start, len(lines)):
        line = lines[index]
        if CHECKBOX_RE.match(line):
            seen_checkbox = True
            continue
        next_line = lines[index + 1] if index + 1 < len(lines) else None
        if is_hard_boundary_line(line, next_line):
            end = index
            break
        if seen_checkbox and is_soft_boundary_line(line):
            end = index
            break
    return content_start, end


def section_text(body: str, headings: tuple[str, ...]) -> str | None:
    """The prose under the first matching heading, or `None` when the heading is absent.

    HTML comments are stripped first, because a PR template's instructions live in them
    (`<!-- state the risk level -->`) and an unfilled template must read as an empty section, not
    as a filled one. A heading present but empty returns `""` — the caller decides whether an
    empty section counts, and every policy check here treats it as unstated.
    """
    lines = HTML_COMMENT_RE.sub("", body or "").splitlines()
    section = find_section(lines, headings)
    if section is None:
        return None
    start, end = section
    return "\n".join(lines[start:end]).strip()


_EMPTY_CHECKBOX_RE = re.compile(r"\[[ xX]?\]")


def has_section_content(body: str, headings: tuple[str, ...], *, min_length: int = 1) -> bool:
    """True when the section exists and carries at least `min_length` characters of real content.

    A bare checkbox with no label, a lone `-` bullet and a heading with nothing under it are not
    content: a check satisfied by an untouched template is the exact failure these rules exist to
    catch. `N/A`, on the other hand, *is* content — somebody read the section and answered it, and
    accusing them of saying nothing would be wrong.
    """
    text = section_text(body, headings)
    if text is None:
        return False
    stripped = _EMPTY_CHECKBOX_RE.sub("", text).strip(_HEADING_STRIP_CHARS + "\n")
    return len(stripped.strip()) >= min_length
