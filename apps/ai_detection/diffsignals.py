"""The diff-level structural kinds (phase 12, stage 6).

`structural.py` reads a pull request's shape from rows the database already holds; `baselines.py`
reads an author's history. The three kinds here need something neither has: the bytes of the
change. The only place those bytes already exist locally is the bare clone the churn job makes, so
this family runs from `apps/churn/diffs.py` inside the nightly churn run, and never during a sync
— it adds no GitHub API call at all.

Everything in this module is pure: it takes a `DiffContext` a caller built from the clone and
returns matches. It opens no database connection and runs no subprocess, which is what lets the
whole family run inside churn's worker threads (`apps/churn/services.py`: a worker thread must
never touch the ORM) and be tested on hand-written diffs with no git repository at all.

Confidence semantics are unchanged: a `SignalRule` can never be `high`, so nothing here can ever
make a pull request `ai_explicit`. Each kind quotes the thresholds that produced it alongside the
measurement, because a lead reading "8% of the change was semantic" cannot judge it without
knowing the rule said 15%.

`DiffFacts` is the other half of the stage: the diff-derived inputs the policy engine consumes
(stage 7) for `QUALITY_GATE_BYPASSED`, `TEST_WEAKENED` and `SECRET_ARTIFACT_COMMITTED`. It stores
counts, paths and codes only. It never stores a matched secret, not even truncated — the point of
noticing a committed credential is not to keep a second copy of it in SQLite.
"""

from __future__ import annotations

import fnmatch
import posixpath
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from apps.ai_detection.evidence import EvidenceCode
from apps.ai_detection.models import SignalKind
from apps.ai_detection.structural import StructuralMatch

# --- the context a caller builds from the clone --------------------------------------------------


@dataclass(frozen=True)
class DiffFile:
    """One file of the change, with the lines the diff added and removed.

    `base_lines` is the file's whole content *before* the change, loaded only for the files the
    comment-density kind can use it for, and empty otherwise — it is the local baseline that kind
    compares against, and a caller under a time budget may legitimately not have it.
    """

    path: str
    added_lines: tuple[str, ...] = ()
    removed_lines: tuple[str, ...] = ()
    base_lines: tuple[str, ...] = ()
    is_test: bool = False
    is_excluded: bool = False


@dataclass(frozen=True)
class DiffTotals:
    """Line counts for the whole change, as `git diff --numstat` reports them.

    `semantic_*` are the same counts under `-w --ignore-blank-lines`: a pure reformat moves
    thousands of lines and almost no semantics, which is exactly the gap `wholesale_reformat`
    measures.
    """

    added: int = 0
    removed: int = 0
    semantic_added: int = 0
    semantic_removed: int = 0

    @property
    def lines(self) -> int:
        return self.added + self.removed

    @property
    def semantic_lines(self) -> int:
        return self.semantic_added + self.semantic_removed


@dataclass(frozen=True)
class DiffContext:
    """Everything a diff kind may read. Deliberately ORM-free — ids and strings only — so it can
    be built and consumed inside a churn worker thread."""

    pull_request_id: int
    files: tuple[DiffFile, ...] = ()
    totals: DiffTotals = field(default_factory=DiffTotals)

    def analysable_files(self) -> tuple[DiffFile, ...]:
        return tuple(f for f in self.files if not f.is_excluded)


@dataclass(frozen=True)
class DiffRuleSpec:
    """The part of an active `SignalRule` a worker thread needs: its identity, its kind and its
    tuned thresholds. Built on the main thread by `apps.ai_detection.services.diff_rule_specs()`
    so no worker ever reads a row or an `AppSetting`."""

    pk: int
    kind: str
    params: dict[str, Any]
    tool: str
    confidence: str


DiffFunction = Callable[[Mapping[str, Any], DiffContext], Iterator[StructuralMatch]]


# --- language awareness -------------------------------------------------------------------------

# Comment syntax per extension, for the density kind and for normalising blocks. Only the prefixes
# a line can *start* with: this is a line classifier, not a parser, and a trailing comment after
# code is code as far as it is concerned.
_LINE_COMMENT_PREFIXES: dict[str, tuple[str, ...]] = {
    ".py": ("#",),
    ".pyi": ("#",),
    ".rb": ("#",),
    ".sh": ("#",),
    ".bash": ("#",),
    ".zsh": ("#",),
    ".yml": ("#",),
    ".yaml": ("#",),
    ".toml": ("#",),
    ".cfg": ("#",),
    ".ini": ("#", ";"),
    ".js": ("//", "/*", "*"),
    ".jsx": ("//", "/*", "*"),
    ".mjs": ("//", "/*", "*"),
    ".cjs": ("//", "/*", "*"),
    ".ts": ("//", "/*", "*"),
    ".tsx": ("//", "/*", "*"),
    ".go": ("//", "/*", "*"),
    ".java": ("//", "/*", "*"),
    ".kt": ("//", "/*", "*"),
    ".rs": ("//", "/*", "*"),
    ".c": ("//", "/*", "*"),
    ".h": ("//", "/*", "*"),
    ".cc": ("//", "/*", "*"),
    ".cpp": ("//", "/*", "*"),
    ".hpp": ("//", "/*", "*"),
    ".cs": ("//", "/*", "*"),
    ".php": ("//", "#", "/*", "*"),
    ".swift": ("//", "/*", "*"),
    ".scala": ("//", "/*", "*"),
    ".scss": ("//", "/*", "*"),
    ".css": ("/*", "*"),
    ".sql": ("--",),
}

# Prose carries no code, so "comment density" over a Markdown file is 100% and means nothing. The
# density kind skips these entirely rather than reading a documentation-only change as an outlier.
_PROSE_SUFFIXES = (".md", ".mdx", ".rst", ".txt", ".adoc")

_DOCSTRING_START_RE = re.compile(r"^(?:[rubf]{0,2})(?:\"\"\"|''')")


def _suffix(path: str) -> str:
    return posixpath.splitext(path)[1].lower()


def _comment_prefixes(path: str) -> tuple[str, ...] | None:
    """The comment prefixes for `path`, or `None` when the file is prose or an unknown format —
    "unknown" must mean "not measured", never "no comments found"."""
    if path.lower().endswith(_PROSE_SUFFIXES):
        return None
    return _LINE_COMMENT_PREFIXES.get(_suffix(path))


def has_comment_syntax(path: str) -> bool:
    """`True` when the comment-density kind can classify this file, which is the only reason a
    caller loads its pre-change contents out of the clone. Public so `apps/churn/diffs.py` can
    spend its `git show` calls on the files that will actually be measured."""
    return _comment_prefixes(path) is not None


def _classify_lines(path: str, lines: Sequence[str]) -> tuple[int, int]:
    """`(comment_lines, code_lines)` for `path`, ignoring blank lines.

    A Python docstring is counted as a comment when the line opens or closes one, which
    under-counts the body of a long docstring. The alternative — tracking quote state across a
    diff that only shows changed lines — would guess, and a kind that quietly guesses is worse
    than one that measures a little less.
    """
    prefixes = _comment_prefixes(path)
    if prefixes is None:
        return 0, 0

    comments = 0
    code = 0
    is_python = _suffix(path) in {".py", ".pyi"}
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith(prefixes) or (is_python and _DOCSTRING_START_RE.match(line)):
            comments += 1
        else:
            code += 1
    return comments, code


def _normalise_for_blocks(path: str, lines: Sequence[str]) -> list[str]:
    """Lines reduced to what a duplicate-detector should compare: no blanks, no comment-only
    lines, whitespace runs collapsed. Indentation and comments are exactly what a tool varies
    between two copies of the same block, so leaving them in would hide the duplication."""
    prefixes = _comment_prefixes(path) or ()
    normalised = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if prefixes and line.startswith(prefixes):
            continue
        normalised.append(re.sub(r"\s+", " ", line))
    return normalised


def _number(params: Mapping[str, Any], key: str, default: float) -> float:
    value = params.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return value


# --- the kinds ----------------------------------------------------------------------------------


def _wholesale_reformat(params: Mapping[str, Any], ctx: DiffContext) -> Iterator[StructuralMatch]:
    """A large diff that barely changes any semantics — a formatter, a line-ending sweep, or a
    tool told to "clean up" a file it was asked to edit.

    One extra `git diff -w --ignore-blank-lines --numstat` against a tree that is already on disk,
    which makes this the cheapest and most reliable of the three diff kinds.
    """
    min_lines = _number(params, "min_lines", 200)
    max_semantic_share = _number(params, "max_semantic_share", 0.15)

    lines = ctx.totals.lines
    if lines < min_lines:
        return
    share = ctx.totals.semantic_lines / lines if lines else 0.0
    if share > max_semantic_share:
        return

    yield StructuralMatch(
        code=EvidenceCode.WHOLESALE_REFORMAT,
        params={
            "lines": lines,
            "semantic_lines": ctx.totals.semantic_lines,
            "share": round(share * 100, 1),
            "min_lines": int(min_lines),
            "max_share": round(max_semantic_share * 100, 1),
        },
    )


def _comment_density_outlier(params: Mapping[str, Any], ctx: DiffContext) -> Iterator[StructuralMatch]:
    """The added code carries far more comment and docstring lines than the code it was added to.

    The baseline is the *same files before the change*, not a repository-wide median: it is the
    comparison a reviewer would make, it is per-language for free, and it costs one `git show` per
    file instead of a walk over the whole tree. A change whose files are all new therefore
    produces nothing — without a baseline there is nothing to be an outlier against, and the
    project's own rule is that a thin sample yields no number rather than a guessed one.

    Test files are skipped: a test file is mostly assertions and explanatory comments by nature.
    """
    min_added_lines = _number(params, "min_added_lines", 60)
    min_baseline_lines = _number(params, "min_baseline_lines", 40)
    min_density = _number(params, "min_density", 0.2)
    ratio = _number(params, "ratio", 2.5)

    added_comments = added_code = base_comments = base_code = 0
    for diff_file in ctx.analysable_files():
        if diff_file.is_test or _comment_prefixes(diff_file.path) is None:
            continue
        comments, code = _classify_lines(diff_file.path, diff_file.added_lines)
        added_comments += comments
        added_code += code
        comments, code = _classify_lines(diff_file.path, diff_file.base_lines)
        base_comments += comments
        base_code += code

    added_total = added_comments + added_code
    base_total = base_comments + base_code
    if added_total < min_added_lines or base_total < min_baseline_lines:
        return

    density = added_comments / added_total
    baseline_density = base_comments / base_total
    if density < min_density:
        return
    # A baseline of exactly zero would make any ratio infinite, so the comparison is made against
    # a floor of one comment line in a hundred — a codebase with no comments at all is a weaker
    # baseline, not an automatic match.
    if density < ratio * max(baseline_density, 0.01):
        return

    yield StructuralMatch(
        code=EvidenceCode.COMMENT_DENSITY_OUTLIER,
        params={
            "comment_lines": added_comments,
            "code_lines": added_code,
            "density": round(density * 100, 1),
            "baseline_density": round(baseline_density * 100, 1),
            "ratio": round(ratio, 1),
            "min_density": round(min_density * 100, 1),
        },
    )


def _duplicated_blocks(params: Mapping[str, Any], ctx: DiffContext) -> Iterator[StructuralMatch]:
    """The same block of added code appears several times in one change.

    Counted over non-overlapping windows, so a run of twenty identical lines is not read as
    thirteen duplicated eight-line blocks, and only across `min_files` distinct files by default:
    a repeated block inside one file is often a legitimate table of cases, whereas the same block
    pasted into three files is the shape of a tool that solved the same problem three times.
    """
    block_lines = int(_number(params, "block_lines", 8))
    min_occurrences = int(_number(params, "min_occurrences", 3))
    min_files = int(_number(params, "min_files", 2))
    if block_lines < 2:
        return

    windows: dict[tuple[str, ...], list[tuple[str, int]]] = {}
    for diff_file in ctx.analysable_files():
        normalised = _normalise_for_blocks(diff_file.path, diff_file.added_lines)
        for start in range(0, len(normalised) - block_lines + 1):
            key = tuple(normalised[start : start + block_lines])
            windows.setdefault(key, []).append((diff_file.path, start))

    best_occurrences = 0
    best_files: set[str] = set()
    for positions in windows.values():
        if len(positions) < min_occurrences:
            continue
        # Greedy non-overlap, per file, over positions already in ascending order within a file.
        taken: dict[str, int] = {}
        occurrences = 0
        files: set[str] = set()
        for path, start in positions:
            if start < taken.get(path, -1):
                continue
            taken[path] = start + block_lines
            occurrences += 1
            files.add(path)
        if occurrences < min_occurrences or len(files) < min_files:
            continue
        if occurrences > best_occurrences:
            best_occurrences, best_files = occurrences, files

    if not best_occurrences:
        return

    yield StructuralMatch(
        code=EvidenceCode.DUPLICATED_BLOCKS,
        params={
            "occurrences": best_occurrences,
            "files": len(best_files),
            "block_lines": block_lines,
            "min_occurrences": min_occurrences,
            "min_files": min_files,
        },
    )


# `name>=1.0`, `name[extra]==1.0`, `"name>=1.0"` — the package name is everything up to the first
# version specifier, extra bracket, quote or comma.
_REQUIREMENT_NAME_RE = re.compile(r"^['\"]?([A-Za-z0-9][A-Za-z0-9._-]*)")
_JSON_DEPENDENCY_RE = re.compile(r"^\"(?P<name>@?[A-Za-z0-9][\w.@/-]*)\"\s*:\s*\"(?P<version>[^\"]*)\"")
_JSON_VERSION_RE = re.compile(r"^(?:[\^~>=<]|\d|\*|latest|workspace:|file:|npm:|git\+)")
_MANIFEST_SKIP_PREFIXES = ("-r ", "--", "#", "[", "}", "{")


def _added_package_names(path: str, added_lines: Sequence[str]) -> list[str]:
    """Package names a manifest diff adds, parsed conservatively — a line this cannot read is
    skipped rather than guessed at, because a wrong package name produces evidence that names a
    dependency nobody added."""
    base = posixpath.basename(path).lower()
    names: list[str] = []
    for raw in added_lines:
        line = raw.strip()
        if not line or line.startswith(_MANIFEST_SKIP_PREFIXES):
            continue
        if base == "package.json":
            match = _JSON_DEPENDENCY_RE.match(line)
            # A dependency entry is `"name": "<version range>"`. Every other string pair in the
            # file — "name", "license", a script command — fails the version test.
            if match and _JSON_VERSION_RE.match(match.group("version").strip()):
                names.append(match.group("name"))
            continue
        if base == "pyproject.toml" and not line.startswith(("'", '"')):
            # Inside a dependency array every entry is a quoted string; `key = value` lines are
            # configuration, not dependencies.
            continue
        match = _REQUIREMENT_NAME_RE.match(line)
        if match:
            names.append(match.group(1))
    return names


def _import_tokens(package: str) -> set[str]:
    """The identifiers a change would mention if it actually used `package`: the name itself, its
    PEP 503 underscore form, and the tail of a scoped npm name."""
    tokens = {package, package.replace("-", "_"), package.replace("_", "-")}
    if package.startswith("@") and "/" in package:
        tokens.add(package.split("/", 1)[1])
    return {token for token in tokens if token}


def _unused_new_dependency(params: Mapping[str, Any], ctx: DiffContext) -> Iterator[StructuralMatch]:
    """A manifest gains a dependency and nothing else in the change mentions it.

    Registered in stage 4 and silent until now: deciding whether anything imports a package needs
    the file contents of the change. The search is deliberately broad — any mention of the name in
    any added line outside the manifests counts as use — because the failure to avoid is accusing
    a change that does use its new dependency, not missing one that does not.
    """
    manifest_patterns = params.get("manifests") or []
    patterns = [str(pattern) for pattern in manifest_patterns]
    max_matches = int(_number(params, "max_matches", 5))

    manifest_files = [
        diff_file
        for diff_file in ctx.files
        if any(
            fnmatch.fnmatch(posixpath.basename(diff_file.path), pattern)
            or fnmatch.fnmatch(diff_file.path, pattern)
            for pattern in patterns
        )
    ]
    if not manifest_files:
        return

    manifest_paths = {diff_file.path for diff_file in manifest_files}
    haystack = "\n".join(
        line
        for diff_file in ctx.files
        if diff_file.path not in manifest_paths
        for line in diff_file.added_lines
    ).lower()

    seen: set[str] = set()
    emitted = 0
    for diff_file in manifest_files:
        for package in _added_package_names(diff_file.path, diff_file.added_lines):
            if package.lower() in seen:
                continue
            seen.add(package.lower())
            if any(token.lower() in haystack for token in _import_tokens(package)):
                continue
            yield StructuralMatch(
                code=EvidenceCode.UNUSED_NEW_DEPENDENCY,
                params={"manifest": diff_file.path, "package": package},
            )
            emitted += 1
            if emitted >= max_matches:
                return


DIFF_FUNCTIONS: dict[str, DiffFunction] = {
    SignalKind.WHOLESALE_REFORMAT: _wholesale_reformat,
    SignalKind.COMMENT_DENSITY_OUTLIER: _comment_density_outlier,
    SignalKind.DUPLICATED_BLOCKS: _duplicated_blocks,
    SignalKind.UNUSED_NEW_DEPENDENCY: _unused_new_dependency,
}


def run_diff_kind(kind: str, params: Mapping[str, Any], ctx: DiffContext) -> Iterator[StructuralMatch]:
    function = DIFF_FUNCTIONS.get(kind)
    if function is None:
        return iter(())
    return function(params, ctx)


def run_diff_rules(rules: Sequence[DiffRuleSpec], ctx: DiffContext) -> list[tuple[int, StructuralMatch]]:
    """Every active diff rule over one change, as `(rule pk, match)` pairs — the shape
    `services.reconcile_diff_signals` writes from."""
    results: list[tuple[int, StructuralMatch]] = []
    for rule in rules:
        for match in run_diff_kind(rule.kind, rule.params, ctx):
            results.append((rule.pk, match))
    return results


# --- diff facts for the policy engine (stage 7) --------------------------------------------------

# Paths whose whole purpose is to run or configure a check. A change to one of them is not a
# violation by itself; it is the reason to look at what the diff did to it.
_QUALITY_GATE_PATTERNS = (
    ".github/workflows/*",
    ".github/actions/*",
    ".gitlab-ci.yml",
    ".pre-commit-config.yaml",
    ".pre-commit-config.yml",
    "Makefile",
    "makefile",
    "tox.ini",
    "noxfile.py",
    ".eslintrc*",
    "eslint.config.*",
    ".ruff.toml",
    "setup.cfg",
    "mypy.ini",
    "sonar-project.properties",
    "codecov.yml",
    ".codecov.yml",
)

# Added lines that switch a check off. Each maps to a stable code, never to a rendered sentence.
_RELAXATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("continue_on_error", re.compile(r"continue-on-error\s*:\s*true", re.IGNORECASE)),
    ("step_disabled", re.compile(r"^\s*if\s*:\s*(?:false|\$\{\{\s*false\s*\}\})\s*$", re.IGNORECASE)),
    ("hook_bypassed", re.compile(r"--no-verify\b")),
    ("failure_swallowed", re.compile(r"\|\|\s*true\b")),
    ("exit_zero", re.compile(r"--exit-zero\b")),
    ("errors_ignored", re.compile(r"ignore_errors\s*=\s*true", re.IGNORECASE)),
    ("coverage_floor_removed", re.compile(r"fail_under\s*=\s*0\b", re.IGNORECASE)),
    ("type_check_relaxed", re.compile(r"strict\s*[:=]\s*false", re.IGNORECASE)),
    ("lint_disabled", re.compile(r"eslint-disable(?!-next-line\s+\S+\s*$)|ruff\s*:\s*noqa", re.IGNORECASE)),
)

# Commands a CI file runs to enforce something. A *removed* line carrying one is a check that no
# longer runs.
_CHECK_COMMAND_RE = re.compile(
    r"\b(pytest|ruff|mypy|flake8|black|eslint|tsc|jest|vitest|go\s+test|cargo\s+test|"
    r"npm\s+(?:run\s+)?test|yarn\s+test|makemigrations\s+--check|manage\.py\s+check)\b",
    re.IGNORECASE,
)

_SKIP_MARKER_RE = re.compile(
    r"@pytest\.mark\.(?:skip|skipif|xfail)|@unittest\.skip|pytest\.skip\(|"
    r"\b(?:it|test|describe|context)\.(?:skip|only)\(|\bt\.Skip\(|#\[ignore\]|@Ignore\b|\.xit\(",
    re.IGNORECASE,
)

_ASSERTION_RE = re.compile(
    r"\bassert\b|\bassert[A-Z]\w*\(|\bexpect\(|\.should\b|\brequire\.\w+\(|\bXCTAssert",
)

# Files that should not be in a repository at all. Matched on the path, so nothing needs to look
# at the contents — which is the point: this code never reads or stores a credential's value.
_SECRET_ARTIFACT_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "*.key",
    "*service-account*.json",
    "credentials.json",
    ".npmrc",
    ".pypirc",
    ".netrc",
)
# The exceptions: a template or an example is the recommended way to document which variables
# exist, and a public key is meant to be public.
_SECRET_ARTIFACT_EXCEPTIONS = (
    "*.example",
    "*.sample",
    "*.template",
    "*.dist",
    "*.pub",
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.test",
)

# High-signal credential shapes. Only the *count* of matching added lines is ever kept.
_SECRET_LINE_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(
        r"(?i)\b(?:secret|password|passwd|token|api[_-]?key|access[_-]?key)\b\s*[:=]\s*"
        r"[\"'][^\"'\s]{12,}[\"']"
    ),
)


@dataclass(frozen=True)
class DiffFacts:
    """What the diff says about this change that the policy engine needs (stage 7).

    Counts, paths and codes only. `secret_like_lines` is a number, and `secret_artifact_paths` are
    paths — the matched text is never carried out of this function, so a violation can say "this
    change adds a file that looks like a credential" without PR Radar storing the credential.
    """

    test_lines_added: int = 0
    test_lines_removed: int = 0
    assertions_removed: int = 0
    skip_markers_added: int = 0
    quality_gate_paths: tuple[str, ...] = ()
    quality_gate_relaxations: tuple[str, ...] = ()
    checks_removed: int = 0
    secret_artifact_paths: tuple[str, ...] = ()
    secret_like_lines: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "test_lines_added": self.test_lines_added,
            "test_lines_removed": self.test_lines_removed,
            "assertions_removed": self.assertions_removed,
            "skip_markers_added": self.skip_markers_added,
            "quality_gate_paths": list(self.quality_gate_paths),
            "quality_gate_relaxations": list(self.quality_gate_relaxations),
            "checks_removed": self.checks_removed,
            "secret_artifact_paths": list(self.secret_artifact_paths),
            "secret_like_lines": self.secret_like_lines,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> DiffFacts:
        """Tolerant by design: a row written by an older version of this dataclass must still
        load, with the keys it lacks at their defaults, rather than break the pull-request page."""
        data = data or {}

        def integer(key: str) -> int:
            value = data.get(key, 0)
            return value if isinstance(value, int) and not isinstance(value, bool) else 0

        def strings(key: str) -> tuple[str, ...]:
            value = data.get(key) or []
            return tuple(str(item) for item in value) if isinstance(value, (list, tuple)) else ()

        return cls(
            test_lines_added=integer("test_lines_added"),
            test_lines_removed=integer("test_lines_removed"),
            assertions_removed=integer("assertions_removed"),
            skip_markers_added=integer("skip_markers_added"),
            quality_gate_paths=strings("quality_gate_paths"),
            quality_gate_relaxations=strings("quality_gate_relaxations"),
            checks_removed=integer("checks_removed"),
            secret_artifact_paths=strings("secret_artifact_paths"),
            secret_like_lines=integer("secret_like_lines"),
        )


def _matches_any(path: str, patterns: Sequence[str]) -> bool:
    base = posixpath.basename(path)
    return any(fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(base, pattern) for pattern in patterns)


def collect_diff_facts(ctx: DiffContext) -> DiffFacts:
    """Reads the change once and records the diff-derived inputs of `QUALITY_GATE_BYPASSED`,
    `TEST_WEAKENED` and `SECRET_ARTIFACT_COMMITTED`.

    It decides nothing. Whether a removed assertion is a violation depends on an `AIPolicy` toggle
    that defaults to off, and that judgement belongs to `apps/policy` — this function only states
    what the diff contains.
    """
    test_added = test_removed = assertions_removed = skips_added = 0
    checks_removed = 0
    secret_like = 0
    gate_paths: list[str] = []
    relaxations: set[str] = set()
    secret_paths: list[str] = []

    for diff_file in ctx.files:
        is_gate = _matches_any(diff_file.path, _QUALITY_GATE_PATTERNS)
        if is_gate:
            gate_paths.append(diff_file.path)

        if _matches_any(diff_file.path, _SECRET_ARTIFACT_PATTERNS) and not _matches_any(
            diff_file.path, _SECRET_ARTIFACT_EXCEPTIONS
        ):
            secret_paths.append(diff_file.path)

        for line in diff_file.added_lines:
            if any(pattern.search(line) for pattern in _SECRET_LINE_PATTERNS):
                secret_like += 1
            if is_gate:
                for code, pattern in _RELAXATION_PATTERNS:
                    if pattern.search(line):
                        relaxations.add(code)
            if diff_file.is_test:
                test_added += 1
                if _SKIP_MARKER_RE.search(line):
                    skips_added += 1

        for line in diff_file.removed_lines:
            if is_gate and _CHECK_COMMAND_RE.search(line):
                checks_removed += 1
            if diff_file.is_test:
                test_removed += 1
                if _ASSERTION_RE.search(line):
                    assertions_removed += 1

    # A removed assertion that the same file adds back is a rewrite, not a weakening; the policy
    # rule reads both counts, so both are reported rather than netted off here.
    return DiffFacts(
        test_lines_added=test_added,
        test_lines_removed=test_removed,
        assertions_removed=assertions_removed,
        skip_markers_added=skips_added,
        quality_gate_paths=tuple(sorted(set(gate_paths))),
        quality_gate_relaxations=tuple(sorted(relaxations)),
        checks_removed=checks_removed,
        secret_artifact_paths=tuple(sorted(set(secret_paths))),
        secret_like_lines=secret_like,
    )
