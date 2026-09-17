"""Content hash over everything that can change what `make css` produces in app.css.

`tests/test_css_tokens.py` recomputes this hash and compares it against the sidecar file
`make css` writes (static/css/.build-manifest.sha256), so an edited template/token/class that
nobody rebuilt CSS for fails the build instead of silently shipping unstyled markup.
"""

import hashlib
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SOURCE_FILES = (
    BASE_DIR / "static/css/tokens.css",
    BASE_DIR / "static/css/src/input.css",
)
SCAN_DIRS = (
    BASE_DIR / "templates",
    BASE_DIR / "apps",
    BASE_DIR / "static/js",
)
SCAN_SUFFIXES = (".html", ".py", ".js")
EXCLUDED_DIR_NAMES = {"migrations", "tests"}


def _scanned_files() -> list[Path]:
    files = list(SOURCE_FILES)
    for scan_dir in SCAN_DIRS:
        files.extend(
            sorted(
                p
                for p in scan_dir.rglob("*")
                if p.suffix in SCAN_SUFFIXES
                and not EXCLUDED_DIR_NAMES & set(p.relative_to(scan_dir).parts[:-1])
            )
        )
    return files


def compute_manifest_hash() -> str:
    digest = hashlib.sha256()
    for path in _scanned_files():
        digest.update(str(path.relative_to(BASE_DIR)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


if __name__ == "__main__":
    print(compute_manifest_hash())
