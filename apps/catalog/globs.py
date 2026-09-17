"""Path glob matching for AppSetting-configured path lists (EXCLUDED_PATH_GLOBS, TEST_PATH_GLOBS).

`PurePath.match()` cannot express `**` on Python 3.12 and `fnmatch` lets `*` cross `/`, so this
translates the small glob dialect the settings use into anchored regexes instead. `*` matches
within one path segment, `?` matches one character, `**` matches any number of whole segments
(including zero). A pattern without a `/` is a basename pattern, matched at any depth; a pattern
with a `/` is matched against the full repo-relative path. Comparison is case-sensitive, like git.
"""

import logging
import re
from collections.abc import Sequence

logger = logging.getLogger(__name__)


def _translate_segment(segment: str) -> str:
    out = []
    for char in segment:
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
    return "".join(out)


def _pattern_to_regex_body(pattern: str) -> str:
    segments = pattern.split("/")
    count = len(segments)
    parts: list[str] = []
    skip_separator = True
    for index, segment in enumerate(segments):
        if segment == "**":
            if count == 1:
                parts.append(".*")
            elif index == 0:
                parts.append("(?:.*/)?")
                skip_separator = True
            else:
                parts.append("(?:/.*)?")
                skip_separator = False
            continue
        if not skip_separator:
            parts.append("/")
        parts.append(_translate_segment(segment))
        skip_separator = False
    return "".join(parts)


def compile_globs(patterns: Sequence[str]) -> list[re.Pattern[str]]:
    """A typo in an AppSetting must not break every sync, so an unusable pattern is skipped
    with a warning rather than raised."""
    compiled: list[re.Pattern[str]] = []
    for raw in patterns:
        try:
            pattern = raw.replace("\\", "/")
            body = _pattern_to_regex_body(pattern)
            regex = body if "/" in pattern else f"(?:.*/)?{body}"
            compiled.append(re.compile(f"^{regex}$"))
        except (TypeError, AttributeError, re.error):
            logger.warning("Skipping invalid path glob %r.", raw)
    return compiled


def matches_any(path: str, compiled: Sequence[re.Pattern[str]]) -> bool:
    normalized = path.replace("\\", "/")
    return any(pattern.match(normalized) for pattern in compiled)
