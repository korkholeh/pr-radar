import re
from pathlib import Path

from scripts.css_manifest import _scanned_files, compute_manifest_hash

BASE_DIR = Path(__file__).resolve().parent.parent

TOKEN_RE = re.compile(r"--([a-z0-9-]+)\s*:")


def test_app_css_contains_every_token_defined_in_tokens_css():
    tokens_css = (BASE_DIR / "static/css/tokens.css").read_text(encoding="utf-8")
    app_css = (BASE_DIR / "static/css/app.css").read_text(encoding="utf-8")

    token_names = sorted(set(TOKEN_RE.findall(tokens_css)))
    assert token_names, "no tokens found in tokens.css — the regex or the file is broken"

    missing = [name for name in token_names if f"--{name}" not in app_css]
    assert not missing, f"tokens missing from static/css/app.css (run `make css`): {missing}"


def test_app_css_is_not_stale():
    """Guards against @import inlining tokens.css making the token-presence test above pass
    by construction: this compares a checksum over every file that can affect the Tailwind
    build (tokens, input.css, and every scanned template/py/js) against the sidecar `make css`
    writes, so an added utility class with no rebuild fails the suite instead of shipping
    unstyled markup."""
    manifest_path = BASE_DIR / "static/css/.build-manifest.sha256"
    assert manifest_path.exists(), "no static/css/.build-manifest.sha256 — run `make css`"
    recorded = manifest_path.read_text(encoding="utf-8").strip()
    assert compute_manifest_hash() == recorded, "static/css/app.css is stale — run `make css` and commit it"


def test_manifest_excludes_migrations_and_tests():
    """Migrations and test modules cannot contain Tailwind utility classes, so editing them
    (e.g. adding a model in a later phase) must not force an unrelated `make css` rerun."""
    scanned = _scanned_files()
    assert not any("migrations" in p.parts for p in scanned)
    assert not any("tests" in p.parts for p in scanned)
