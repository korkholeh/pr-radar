"""T6: WCAG 2.1 AA contrast for every declared token pair, both themes (plan §3,
ARCHITECTURE line 308). `tokens.css` is the only file allowed a colour literal
(`tests/test_no_hardcoded_colors.py`), so this test parses it directly rather than rendering
anything — a declared pair table, not a full token×token cross-product, because most tokens never
touch each other on screen and a cross-product would force meaningless palette changes.

Two named exemptions, asserted rather than silently omitted:
- `--border` / `--grid` are decorative dividers and chart gridlines, not UI-component boundaries or
  state indicators, so WCAG 2.1 SC 1.4.11 (non-text contrast) does not apply to them. Anything that
  *is* a control boundary (input/select/textarea/button borders, focus rings) uses the new
  `--border-strong` instead (checked by the `interactive_boundaries` group below).
- `--heat-1..4` fills are a redundant encoding: every heat cell also prints its count as text
  (`reviewer_heat_map.html`), so what must pass AA is the *text on* the heat colour, not the fill
  against the page background — that is the `inverted_text` group's `on-heat`/`heat-3`,
  `on-heat`/`heat-4` and `text`/`heat-0..2` pairs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TOKENS_PATH = Path(__file__).resolve().parent.parent / "static" / "css" / "tokens.css"

_BLOCK_RE = re.compile(r"([^{}]+)\{([^{}]+)\}")
_TOKEN_RE = re.compile(r"--([\w-]+)\s*:\s*(#[0-9a-fA-F]{6})")


def _parse_tokens() -> dict[str, dict[str, str]]:
    text = TOKENS_PATH.read_text()
    themes: dict[str, dict[str, str]] = {"light": {}, "dark": {}}
    for selector, body in _BLOCK_RE.findall(text):
        theme = "dark" if "dark" in selector else "light"
        themes[theme].update(dict(_TOKEN_RE.findall(body)))
    return themes


def _srgb_to_linear(channel: float) -> float:
    channel /= 255
    return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    r, g, b = (_srgb_to_linear(channel) for channel in (r, g, b))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    luminance_a, luminance_b = _relative_luminance(hex_a), _relative_luminance(hex_b)
    lighter, darker = max(luminance_a, luminance_b), min(luminance_a, luminance_b)
    return (lighter + 0.05) / (darker + 0.05)


THEMES = _parse_tokens()

_BACKGROUNDS = ("bg", "surface", "surface-2")

_TEXT_PAIRS: list[tuple[str, str]] = [(fg, bg) for fg in ("text", "text-muted") for bg in _BACKGROUNDS]
_STATUS_TEXT_PAIRS: list[tuple[str, str]] = [
    (fg, bg) for fg in ("good", "bad", "warning", "neutral", "accent") for bg in _BACKGROUNDS
]
_INVERTED_TEXT_PAIRS: list[tuple[str, str]] = [
    ("on-accent", "accent"),
    # The info-tip bubble (`.info-tip-bubble`) and the Chart.js tooltip both print `--text` on
    # `--tooltip-bg`, a popover surface that follows the theme — real body text, so AA applies.
    ("text", "tooltip-bg"),
    ("text-muted", "tooltip-bg"),
    ("on-heat", "heat-3"),
    ("on-heat", "heat-4"),
    ("text", "heat-0"),
    ("text", "heat-1"),
    ("text", "heat-2"),
]
_INTERACTIVE_BOUNDARY_PAIRS: list[tuple[str, str]] = [("border-strong", bg) for bg in _BACKGROUNDS] + [
    ("accent", "bg"),
    ("accent", "surface"),
]
_CHART_MARK_TOKENS = ("series-ai", "series-non-ai", *(f"series-{n}" for n in range(1, 9)))
_CHART_MARK_PAIRS: list[tuple[str, str]] = [
    (mark, bg) for mark in _CHART_MARK_TOKENS for bg in ("bg", "surface")
]


def _cases(pairs: list[tuple[str, str]], required: float) -> list[tuple[str, str, str, float]]:
    return [(theme, fg, bg, required) for theme in ("light", "dark") for fg, bg in pairs]


ALL_CASES = (
    _cases(_TEXT_PAIRS, 4.5)
    + _cases(_STATUS_TEXT_PAIRS, 4.5)
    + _cases(_INVERTED_TEXT_PAIRS, 4.5)
    + _cases(_INTERACTIVE_BOUNDARY_PAIRS, 3.0)
    + _cases(_CHART_MARK_PAIRS, 3.0)
)


def _ids() -> list[str]:
    return [f"{theme}:{fg}-on-{bg}>={required}" for theme, fg, bg, required in ALL_CASES]


@pytest.mark.parametrize("theme,fg,bg,required", ALL_CASES, ids=_ids())
def test_token_pair_meets_wcag_aa(theme: str, fg: str, bg: str, required: float) -> None:
    tokens = THEMES[theme]
    ratio = contrast_ratio(tokens[fg], tokens[bg])
    assert ratio >= required, (
        f"{theme} --{fg} on --{bg} is {ratio:.2f}:1, needs >= {required}:1 ({tokens[fg]} on {tokens[bg]})"
    )


def test_border_and_grid_are_exempt_as_decorative_dividers() -> None:
    """`--border`/`--grid` are dividers and chart gridlines (SC 1.4.11 does not cover them) — this
    test only proves the tokens exist, it does not assert a ratio for them."""
    for theme in ("light", "dark"):
        assert "border" in THEMES[theme]
        assert "grid" in THEMES[theme]


def test_heat_fill_itself_is_exempt_the_text_on_it_is_tested_above() -> None:
    """`--heat-1..4` fills are redundant encodings (every heat cell also prints its count as
    text) — the fill-against-background ratio is deliberately not asserted; `on-heat`/`heat-3`,
    `on-heat`/`heat-4` and `text`/`heat-0..2` above are what actually has to pass."""
    for theme in ("light", "dark"):
        for level in range(5):
            assert f"heat-{level}" in THEMES[theme]
