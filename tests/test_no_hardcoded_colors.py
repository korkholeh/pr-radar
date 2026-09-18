import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgb\(|rgba\(|hsl\(|hsla\(")

# Tailwind palette utilities (e.g. `text-white`, `bg-gray-500`) are colour literals in the sense
# CLAUDE.md forbids even though they contain no #hex/rgb()/hsl() text — they bypass the token
# system and are invisible to designers switching a token's value between light and dark.
TAILWIND_PALETTE_CLASS_RE = re.compile(
    r"\b(?:bg|text|border|ring|fill|stroke|from|via|to|outline|decoration|divide|accent|caret)-"
    r"(?:white|black|"
    r"slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|"
    r"blue|indigo|violet|purple|fuchsia|pink|rose)"
    r"(?:-\d{2,3})?\b"
)

SCAN_DIRS = ["templates", "apps", "static/js"]
SCAN_CSS_DIR = "static/css"

EXCLUDED_CSS_FILES = {"tokens.css", "app.css"}
EXCLUDED_DIR_PARTS = {"vendor", "migrations", "__pycache__"}
# CLAUDE.md's token rule is about the web UI's light/dark theming; an exported .xlsx file is
# opened in Excel, which has no notion of the app's CSS custom properties, so spec §10.6's
# "conditional formatting of deltas (green/red by direction)" is necessarily a literal RGB colour
# picked once, independent of the reader's dashboard theme — logged in DECISIONS (p08/review_fix2).
# Exempted line by line (each carries this marker), not by whole file, so any *other* literal
# later added to the same file still fails the build (round 2 audit MINOR #3).
_LINE_EXEMPT_MARKER = "color-literal-exempt"


def _iter_files():
    for scan_dir in SCAN_DIRS:
        root = BASE_DIR / scan_dir
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if set(path.parts) & EXCLUDED_DIR_PARTS:
                continue
            if path.suffix in {".html", ".py", ".js", ".css"}:
                yield path

    css_root = BASE_DIR / SCAN_CSS_DIR
    for path in css_root.rglob("*.css"):
        if path.name in EXCLUDED_CSS_FILES and path.parent == css_root:
            continue
        if "vendor" in path.parts:
            continue
        yield path


def test_no_color_literals_outside_tokens_css():
    violations = []
    for path in _iter_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if COLOR_RE.search(line) and _LINE_EXEMPT_MARKER not in line:
                violations.append(f"{path.relative_to(BASE_DIR)}:{lineno}: {line.strip()}")
    assert not violations, "Color literals found outside tokens.css:\n" + "\n".join(violations)


def test_no_tailwind_palette_color_classes():
    """`text-white`, `bg-gray-500` and friends are colour literals that bypass the token
    system just as surely as a raw #hex — they read the same in light and dark mode."""
    violations = []
    for path in _iter_files():
        if path.suffix == ".css":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if TAILWIND_PALETTE_CLASS_RE.search(line):
                violations.append(f"{path.relative_to(BASE_DIR)}:{lineno}: {line.strip()}")
    assert not violations, "Tailwind palette colour classes found outside the token system:\n" + "\n".join(
        violations
    )
