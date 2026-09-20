"""The diff kinds and `DiffFacts` (phase 12, stage 6).

Everything here runs on hand-written diffs with no git repository and no database: that is the
whole point of keeping `diffsignals.py` pure and leaving the `git` half in `apps/churn/diffs.py`
(which has its own tests against a real temporary repository).

Every kind gets a firing case and a near-miss. These heuristics read an honest developer's work as
readily as a machine's — a deliberate formatting pass, a well-documented module, a table of
similar handlers — which is why none of them may be `high` confidence and why two distinct kinds
are needed before a pull request's AI status moves.
"""

from apps.ai_detection.diffsignals import (
    DIFF_FUNCTIONS,
    DiffContext,
    DiffFacts,
    DiffFile,
    DiffRuleSpec,
    DiffTotals,
    collect_diff_facts,
    run_diff_kind,
    run_diff_rules,
)
from apps.ai_detection.evidence import EVIDENCE_MESSAGES, EvidenceCode
from apps.ai_detection.models import SignalFamily, SignalKind, kinds_in_family


def _ctx(files=(), **totals) -> DiffContext:
    return DiffContext(pull_request_id=1, files=tuple(files), totals=DiffTotals(**totals))


def _py_lines(count: int, prefix: str = "value") -> tuple[str, ...]:
    return tuple(f"{prefix}_{index} = {index}" for index in range(count))


def _comment_lines(count: int) -> tuple[str, ...]:
    return tuple(f"# explanation number {index}" for index in range(count))


def test_every_diff_kind_has_a_function_and_an_evidence_message():
    diff_kinds = kinds_in_family(SignalFamily.DIFF)
    assert set(DIFF_FUNCTIONS) == diff_kinds
    for kind in diff_kinds:
        assert kind in EVIDENCE_MESSAGES, kind


def test_an_unknown_kind_yields_nothing_rather_than_raising():
    """A row written by a future version of the app must not break the run that reads it."""
    assert list(run_diff_kind("reads_minds", {}, _ctx())) == []


# -- wholesale_reformat --------------------------------------------------------------------------


def test_wholesale_reformat_fires_when_almost_nothing_semantic_changed():
    ctx = _ctx(added=400, removed=400, semantic_added=20, semantic_removed=20)

    matches = list(run_diff_kind(SignalKind.WHOLESALE_REFORMAT, {}, ctx))

    assert len(matches) == 1
    assert matches[0].code == EvidenceCode.WHOLESALE_REFORMAT
    assert matches[0].params["lines"] == 800
    assert matches[0].params["semantic_lines"] == 40
    assert matches[0].params["share"] == 5.0
    # The thresholds travel with the measurement, so the evidence can be judged.
    assert matches[0].params["min_lines"] == 200
    assert matches[0].params["max_share"] == 15.0


def test_wholesale_reformat_ignores_a_change_that_is_mostly_semantic():
    ctx = _ctx(added=400, removed=400, semantic_added=300, semantic_removed=300)
    assert list(run_diff_kind(SignalKind.WHOLESALE_REFORMAT, {}, ctx)) == []


def test_wholesale_reformat_ignores_a_small_change():
    """A three-line whitespace fix is not a reformatting sweep; below `min_lines` nothing fires."""
    ctx = _ctx(added=2, removed=1, semantic_added=0, semantic_removed=0)
    assert list(run_diff_kind(SignalKind.WHOLESALE_REFORMAT, {}, ctx)) == []


def test_wholesale_reformat_respects_a_tuned_threshold():
    ctx = _ctx(added=60, removed=0, semantic_added=5, semantic_removed=0)

    assert list(run_diff_kind(SignalKind.WHOLESALE_REFORMAT, {}, ctx)) == []
    assert list(run_diff_kind(SignalKind.WHOLESALE_REFORMAT, {"min_lines": 50}, ctx))


# -- comment_density_outlier ---------------------------------------------------------------------


def test_comment_density_outlier_fires_against_the_files_own_baseline():
    diff_file = DiffFile(
        path="apps/thing/service.py",
        added_lines=_comment_lines(40) + _py_lines(40),
        base_lines=_comment_lines(4) + _py_lines(96),
    )

    matches = list(run_diff_kind(SignalKind.COMMENT_DENSITY_OUTLIER, {}, _ctx([diff_file])))

    assert len(matches) == 1
    assert matches[0].code == EvidenceCode.COMMENT_DENSITY_OUTLIER
    assert matches[0].params["comment_lines"] == 40
    assert matches[0].params["code_lines"] == 40
    assert matches[0].params["density"] == 50.0
    assert matches[0].params["baseline_density"] == 4.0


def test_comment_density_outlier_does_not_fire_on_an_already_well_commented_codebase():
    """The comparison is against the same files before the change. A team that comments heavily
    keeps commenting heavily, and that is not evidence of anything."""
    diff_file = DiffFile(
        path="apps/thing/service.py",
        added_lines=_comment_lines(40) + _py_lines(40),
        base_lines=_comment_lines(45) + _py_lines(55),
    )
    assert list(run_diff_kind(SignalKind.COMMENT_DENSITY_OUTLIER, {}, _ctx([diff_file]))) == []


def test_comment_density_outlier_says_nothing_about_a_change_made_only_of_new_files():
    """No baseline, no outlier. A thin sample yields no number rather than a guessed one — the
    same rule the metrics registry follows."""
    diff_file = DiffFile(path="apps/new/module.py", added_lines=_comment_lines(60) + _py_lines(20))
    assert list(run_diff_kind(SignalKind.COMMENT_DENSITY_OUTLIER, {}, _ctx([diff_file]))) == []


def test_comment_density_outlier_skips_tests_and_prose():
    """A test file is assertions and explanation by nature, and a Markdown file is 100% prose —
    measuring either would report a number that means nothing."""
    test_file = DiffFile(
        path="tests/test_thing.py",
        added_lines=_comment_lines(60) + _py_lines(10),
        base_lines=_py_lines(100),
        is_test=True,
    )
    prose = DiffFile(
        path="docs/guide.md",
        added_lines=tuple(f"Some prose line {index}." for index in range(80)),
        base_lines=tuple(f"Old prose {index}." for index in range(80)),
    )
    assert list(run_diff_kind(SignalKind.COMMENT_DENSITY_OUTLIER, {}, _ctx([test_file, prose]))) == []


def test_comment_density_outlier_ignores_excluded_files():
    excluded = DiffFile(
        path="vendor/thing/generated.py",
        added_lines=_comment_lines(60) + _py_lines(10),
        base_lines=_py_lines(100),
        is_excluded=True,
    )
    assert list(run_diff_kind(SignalKind.COMMENT_DENSITY_OUTLIER, {}, _ctx([excluded]))) == []


def test_comment_density_outlier_counts_python_docstrings_as_comments():
    diff_file = DiffFile(
        path="apps/thing/service.py",
        added_lines=tuple('    """What this does."""' for _ in range(40)) + _py_lines(40),
        base_lines=_comment_lines(2) + _py_lines(98),
    )
    matches = list(run_diff_kind(SignalKind.COMMENT_DENSITY_OUTLIER, {}, _ctx([diff_file])))
    assert matches and matches[0].params["comment_lines"] == 40


# -- duplicated_blocks ---------------------------------------------------------------------------


_BLOCK = (
    "def handle(request):",
    "    payload = parse(request)",
    "    if not payload:",
    "        raise Invalid()",
    "    record = Store.create(payload)",
    "    audit(record)",
    "    notify(record)",
    "    return record",
)


def test_duplicated_blocks_fires_on_the_same_block_in_three_files():
    files = [DiffFile(path=f"apps/mod/{name}.py", added_lines=_BLOCK) for name in ("a", "b", "c")]

    matches = list(run_diff_kind(SignalKind.DUPLICATED_BLOCKS, {}, _ctx(files)))

    assert len(matches) == 1
    assert matches[0].code == EvidenceCode.DUPLICATED_BLOCKS
    assert matches[0].params["occurrences"] == 3
    assert matches[0].params["files"] == 3


def test_duplicated_blocks_matches_across_differing_indentation_and_comments():
    """Indentation and comments are exactly what varies between two copies of a generated block,
    so normalisation removes both before comparing."""
    files = [
        DiffFile(path="apps/mod/a.py", added_lines=_BLOCK),
        DiffFile(path="apps/mod/b.py", added_lines=tuple(f"    {line}" for line in _BLOCK)),
        DiffFile(
            path="apps/mod/c.py",
            added_lines=("# copied from a.py", *_BLOCK),
        ),
    ]

    matches = list(run_diff_kind(SignalKind.DUPLICATED_BLOCKS, {}, _ctx(files)))

    assert matches and matches[0].params["occurrences"] == 3


def test_duplicated_blocks_ignores_repetition_inside_one_file_by_default():
    """A table of similar cases in one file is ordinary code; `min_files` is 2 for that reason."""
    one_file = DiffFile(path="apps/mod/a.py", added_lines=_BLOCK * 3)
    assert list(run_diff_kind(SignalKind.DUPLICATED_BLOCKS, {}, _ctx([one_file]))) == []
    assert list(run_diff_kind(SignalKind.DUPLICATED_BLOCKS, {"min_files": 1}, _ctx([one_file])))


def test_duplicated_blocks_counts_non_overlapping_windows_only():
    """Sixteen identical lines in a file are two eight-line blocks, not nine overlapping ones."""
    files = [DiffFile(path=f"apps/mod/{name}.py", added_lines=_BLOCK * 2) for name in ("a", "b")]

    matches = list(run_diff_kind(SignalKind.DUPLICATED_BLOCKS, {}, _ctx(files)))

    assert matches and matches[0].params["occurrences"] == 4


def test_duplicated_blocks_does_not_fire_on_distinct_code():
    files = [
        DiffFile(path=f"apps/mod/{name}.py", added_lines=_py_lines(20, prefix=name))
        for name in ("a", "b", "c")
    ]
    assert list(run_diff_kind(SignalKind.DUPLICATED_BLOCKS, {}, _ctx(files))) == []


# -- unused_new_dependency -----------------------------------------------------------------------

_MANIFESTS = {"manifests": ["pyproject.toml", "package.json", "requirements*.txt"]}


def test_unused_new_dependency_fires_when_nothing_imports_the_package():
    files = [
        DiffFile(path="pyproject.toml", added_lines=('    "httpx>=0.27",',)),
        DiffFile(path="apps/thing/service.py", added_lines=("import json", "value = json.dumps({})")),
    ]

    matches = list(run_diff_kind(SignalKind.UNUSED_NEW_DEPENDENCY, _MANIFESTS, _ctx(files)))

    assert [match.params["package"] for match in matches] == ["httpx"]
    assert matches[0].params["manifest"] == "pyproject.toml"


def test_unused_new_dependency_is_silent_when_the_change_uses_the_package():
    files = [
        DiffFile(path="pyproject.toml", added_lines=('    "httpx>=0.27",',)),
        DiffFile(path="apps/thing/client.py", added_lines=("import httpx",)),
    ]
    assert list(run_diff_kind(SignalKind.UNUSED_NEW_DEPENDENCY, _MANIFESTS, _ctx(files))) == []


def test_unused_new_dependency_matches_the_underscore_form_of_a_package_name():
    """`ruamel-yaml` on PyPI is `ruamel_yaml` in an import, and a rule that missed that would
    accuse every change that legitimately uses one."""
    files = [
        DiffFile(path="requirements.txt", added_lines=("ruamel-yaml==0.18.6",)),
        DiffFile(path="apps/thing/load.py", added_lines=("from ruamel_yaml import YAML",)),
    ]
    assert list(run_diff_kind(SignalKind.UNUSED_NEW_DEPENDENCY, _MANIFESTS, _ctx(files))) == []


def test_unused_new_dependency_reads_package_json_dependencies_but_not_its_other_keys():
    files = [
        DiffFile(
            path="package.json",
            added_lines=(
                '    "name": "my-app",',
                '    "license": "MIT",',
                '    "lodash": "^4.17.21",',
            ),
        ),
        DiffFile(path="src/index.ts", added_lines=("console.log('hi')",)),
    ]

    matches = list(run_diff_kind(SignalKind.UNUSED_NEW_DEPENDENCY, _MANIFESTS, _ctx(files)))

    assert [match.params["package"] for match in matches] == ["lodash"]


def test_unused_new_dependency_reads_a_scoped_npm_package_by_its_tail():
    files = [
        DiffFile(path="package.json", added_lines=('    "@scope/widget": "^1.0.0",',)),
        DiffFile(path="src/index.ts", added_lines=("import { Widget } from 'widget'",)),
    ]
    assert list(run_diff_kind(SignalKind.UNUSED_NEW_DEPENDENCY, _MANIFESTS, _ctx(files))) == []


def test_unused_new_dependency_is_bounded_by_max_matches():
    """A lockfile-sized manifest change must not write a hundred signal rows for one pull
    request."""
    files = [
        DiffFile(
            path="requirements.txt",
            added_lines=tuple(f"package{index}==1.0" for index in range(20)),
        )
    ]

    matches = list(run_diff_kind(SignalKind.UNUSED_NEW_DEPENDENCY, _MANIFESTS, _ctx(files)))

    assert len(matches) == 5


def test_unused_new_dependency_needs_a_manifest_in_the_change():
    files = [DiffFile(path="apps/thing/service.py", added_lines=("import httpx",))]
    assert list(run_diff_kind(SignalKind.UNUSED_NEW_DEPENDENCY, _MANIFESTS, _ctx(files))) == []


# -- running several rules at once ---------------------------------------------------------------


def test_run_diff_rules_tags_every_match_with_its_rule():
    rules = [
        DiffRuleSpec(pk=7, kind=SignalKind.WHOLESALE_REFORMAT, params={}, tool="other", confidence="medium"),
        DiffRuleSpec(pk=9, kind=SignalKind.DUPLICATED_BLOCKS, params={}, tool="other", confidence="low"),
    ]
    ctx = DiffContext(
        pull_request_id=1,
        files=tuple(DiffFile(path=f"apps/mod/{n}.py", added_lines=_BLOCK) for n in ("a", "b", "c")),
        totals=DiffTotals(added=400, removed=0, semantic_added=10, semantic_removed=0),
    )

    results = run_diff_rules(rules, ctx)

    assert {pk for pk, _ in results} == {7, 9}


def test_a_rule_whose_kind_finds_nothing_contributes_nothing():
    rules = [
        DiffRuleSpec(pk=7, kind=SignalKind.WHOLESALE_REFORMAT, params={}, tool="other", confidence="medium")
    ]
    assert run_diff_rules(rules, _ctx()) == []


# -- DiffFacts: the policy engine's inputs (stage 7) ---------------------------------------------


def test_diff_facts_count_weakened_tests():
    test_file = DiffFile(
        path="tests/test_thing.py",
        added_lines=("@pytest.mark.skip(reason='flaky')", "def test_thing():", "    pass"),
        removed_lines=("    assert result == 3", "    assert other == 4"),
        is_test=True,
    )

    facts = collect_diff_facts(_ctx([test_file]))

    assert facts.skip_markers_added == 1
    assert facts.assertions_removed == 2
    assert facts.test_lines_added == 3
    assert facts.test_lines_removed == 2


def test_diff_facts_only_count_test_changes_in_test_files():
    """An `assert` removed from production code is a code change, not a weakened test."""
    source = DiffFile(path="apps/thing/service.py", removed_lines=("    assert value is not None",))
    facts = collect_diff_facts(_ctx([source]))
    assert facts.assertions_removed == 0
    assert facts.test_lines_removed == 0


def test_diff_facts_record_quality_gate_relaxations_as_codes():
    """Codes, not sentences: the violation is rendered in the reader's language at read time
    (CLAUDE.md), so nothing English is ever stored."""
    workflow = DiffFile(
        path=".github/workflows/ci.yml",
        added_lines=("        continue-on-error: true", "        run: ruff check . || true"),
        removed_lines=("        run: uv run pytest -q",),
    )

    facts = collect_diff_facts(_ctx([workflow]))

    assert facts.quality_gate_paths == (".github/workflows/ci.yml",)
    assert set(facts.quality_gate_relaxations) == {"continue_on_error", "failure_swallowed"}
    assert facts.checks_removed == 1


def test_diff_facts_ignore_a_relaxation_pattern_outside_a_quality_gate_file():
    """`|| true` in an ordinary shell script is somebody's script, not a bypassed gate."""
    script = DiffFile(path="scripts/local.sh", added_lines=("rm -f tmp || true",))
    facts = collect_diff_facts(_ctx([script]))
    assert facts.quality_gate_paths == ()
    assert facts.quality_gate_relaxations == ()


def test_diff_facts_flag_a_committed_credential_file_but_not_its_example():
    files = [
        DiffFile(path=".env", added_lines=("SECRET_KEY=not-a-real-value",)),
        DiffFile(path=".env.example", added_lines=("SECRET_KEY=",)),
        DiffFile(path="deploy/server.pem", added_lines=("-----BEGIN PRIVATE KEY-----",)),
        DiffFile(path="deploy/server.pub", added_lines=("ssh-rsa AAAA",)),
    ]

    facts = collect_diff_facts(_ctx(files))

    assert facts.secret_artifact_paths == (".env", "deploy/server.pem")


def test_diff_facts_count_credential_shaped_lines_without_storing_them():
    """The count is the evidence. Keeping the matched text would mean PR Radar holds a second
    copy of a leaked credential, which is the opposite of the point."""
    token = "ghp_" + "a" * 30
    diff_file = DiffFile(path="apps/thing/client.py", added_lines=(f'TOKEN = "{token}"',))

    facts = collect_diff_facts(_ctx([diff_file]))

    assert facts.secret_like_lines == 1
    assert token not in str(facts.as_dict())


def test_diff_facts_survive_a_round_trip_through_json():
    facts = DiffFacts(
        test_lines_added=3,
        assertions_removed=1,
        quality_gate_paths=("Makefile",),
        quality_gate_relaxations=("hook_bypassed",),
        secret_like_lines=2,
    )
    assert DiffFacts.from_dict(facts.as_dict()) == facts


def test_diff_facts_load_tolerantly_from_an_older_row():
    """A row written before a field existed must still load — a stored analysis can be older than
    the code reading it, and it must never break the page that shows it."""
    facts = DiffFacts.from_dict({"test_lines_added": 4, "unknown_future_key": 9})
    assert facts.test_lines_added == 4
    assert facts.quality_gate_paths == ()


def test_diff_facts_ignore_a_malformed_stored_value():
    facts = DiffFacts.from_dict({"test_lines_added": "lots", "quality_gate_paths": "Makefile"})
    assert facts.test_lines_added == 0
    assert facts.quality_gate_paths == ()
