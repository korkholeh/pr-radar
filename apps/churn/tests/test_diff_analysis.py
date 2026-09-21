"""Diff analysis against a real temporary repository (phase 12, stage 6).

The kinds themselves are tested on hand-written diffs in
`apps/ai_detection/tests/test_diffsignals.py`. What is tested here is everything that needs actual
git: resolving a merge commit's base, the two `numstat` passes that make `wholesale_reformat`
possible, the unified-diff parse, the baseline `git show`, and the way the whole thing rides along
inside `run_churn` — opt-in, written on the main thread, idempotent, and with no credential
anywhere near a `git` argv.

Its own origin repository rather than the session-wide `git_origin`: the hand-counted line totals
in `conftest.py` are what the churn tests assert against, and a commit added for a reformat case
would quietly change them.
"""

from __future__ import annotations

import datetime
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from apps.activity.factories import PRFileFactory, PullRequestFactory
from apps.activity.models import PullRequest
from apps.ai_detection.diffsignals import DiffFacts, DiffRuleSpec
from apps.ai_detection.factories import SignalRuleFactory
from apps.ai_detection.models import AISignal, Confidence, DiffAnalysis, SignalKind, SignalRule
from apps.catalog.factories import RepositoryFactory
from apps.catalog.services import set_setting
from apps.churn.diffs import analyse_pull_request_diff, load_diff_context
from apps.churn.gitcmd import run_git
from apps.churn.services import run_churn
from apps.connections.factories import GitHubConnectionFactory
from apps.connections.services import set_token

pytestmark = pytest.mark.django_db

UTC = datetime.UTC
TOKEN = "ghp_DiffAnalysisSecretValue01234"
FORBIDDEN = TOKEN[:-4]
_GIT_IDENTITY = ["-c", "user.name=Diff Test", "-c", "user.email=diff-test@example.com"]

_BLOCK = [
    "def handle(request):",
    "    payload = parse(request)",
    "    if not payload:",
    "        raise Invalid()",
    "    record = Store.create(payload)",
    "    audit(record)",
    "    notify(record)",
    "    return record",
]


def _commit(repo_dir: Path, message: str, when: datetime.datetime, files: dict[str, str]) -> str:
    for path, content in files.items():
        full = repo_dir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    run_git(["-C", str(repo_dir), "add", "-A"])
    date_str = when.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    previous = {key: os.environ.get(key) for key in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE")}
    os.environ["GIT_AUTHOR_DATE"] = date_str
    os.environ["GIT_COMMITTER_DATE"] = date_str
    try:
        run_git(["-C", str(repo_dir), *_GIT_IDENTITY, "commit", "-m", message])
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return run_git(["-C", str(repo_dir), "rev-parse", "HEAD"]).strip()


@dataclass(frozen=True)
class DiffOrigin:
    path: Path
    reformat_sha: str
    reformat_merged_at: datetime.datetime
    duplicate_sha: str
    duplicate_merged_at: datetime.datetime
    dependency_sha: str
    dependency_merged_at: datetime.datetime


@pytest.fixture(scope="session")
def diff_origin(tmp_path_factory: pytest.TempPathFactory) -> DiffOrigin:
    """Three commits, each the whole content of one pull request:

    1. `src/app.py` reindented — 120 lines moved, nothing semantic changed.
    2. `src/a.py`, `src/b.py`, `src/c.py`, each carrying the same eight-line block.
    3. `requirements.txt` gains `httpx`, and no file in that change mentions it.
    """
    repo_dir = tmp_path_factory.mktemp("diff_origin")
    run_git(["init", "--quiet", "-b", "main", str(repo_dir)])

    original = "\n".join(f"value_{index} = {index}" for index in range(120)) + "\n"
    _commit(
        repo_dir,
        "initial",
        datetime.datetime(2026, 1, 1, tzinfo=UTC),
        {"src/app.py": original, ".github/workflows/ci.yml": "jobs:\n  test:\n    run: pytest -q\n"},
    )

    reindented = "\n".join(f"    value_{index}   =   {index}" for index in range(120)) + "\n"
    reformat_sha = _commit(
        repo_dir, "Reformat app.py", datetime.datetime(2026, 1, 2, tzinfo=UTC), {"src/app.py": reindented}
    )

    block = "\n".join(_BLOCK) + "\n"
    duplicate_sha = _commit(
        repo_dir,
        "Add three handlers",
        datetime.datetime(2026, 1, 3, tzinfo=UTC),
        {"src/a.py": block, "src/b.py": block, "src/c.py": block},
    )

    # Two dependencies added, one of them imported by the same change: the detector must report
    # the unused one and stay quiet about the used one, which is the whole point of the check.
    dependency_sha = _commit(
        repo_dir,
        "Add a dependency",
        datetime.datetime(2026, 1, 4, tzinfo=UTC),
        {
            "requirements.txt": "django==5.2\nhttpx==0.27.0\n",
            "src/views.py": "import django\n\nSETTINGS = django.conf.settings\n",
        },
    )

    _commit(
        repo_dir,
        "Trailing commit",
        datetime.datetime(2026, 2, 1, tzinfo=UTC),
        {"README.md": "diff origin\n"},
    )

    return DiffOrigin(
        path=repo_dir,
        reformat_sha=reformat_sha,
        reformat_merged_at=datetime.datetime(2026, 1, 2, tzinfo=UTC),
        duplicate_sha=duplicate_sha,
        duplicate_merged_at=datetime.datetime(2026, 1, 3, tzinfo=UTC),
        dependency_sha=dependency_sha,
        dependency_merged_at=datetime.datetime(2026, 1, 4, tzinfo=UTC),
    )


@pytest.fixture(autouse=True)
def _data_dir_in_tmp_path(settings, tmp_path):
    settings.DATA_DIR = tmp_path


@pytest.fixture
def diff_remote(monkeypatch: pytest.MonkeyPatch, diff_origin: DiffOrigin) -> DiffOrigin:
    import apps.churn.clones as clones_module

    monkeypatch.setattr(clones_module, "remote_url_for", lambda repository: f"file://{diff_origin.path}")
    return diff_origin


@pytest.fixture
def repository(diff_remote):
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    return RepositoryFactory(connection=connection, full_name="acme/diffs", default_branch="main")


@pytest.fixture
def clone(repository, diff_remote):
    from apps.churn.clones import ensure_clone

    return ensure_clone(repository, None)


def _pr(repository, sha, merged_at, paths: dict[str, bool]) -> PullRequest:
    """`paths` maps path -> is_test."""
    pr = PullRequestFactory(
        repository=repository,
        state=PullRequest.State.MERGED,
        merge_method=PullRequest.MergeMethod.SQUASH,
        merge_commit_sha=sha,
        merged_at=merged_at,
    )
    for path, is_test in paths.items():
        PRFileFactory(pull_request=pr, path=path, is_test=is_test)
    return pr


def _reformat_pr(repository, diff_origin) -> PullRequest:
    return _pr(repository, diff_origin.reformat_sha, diff_origin.reformat_merged_at, {"src/app.py": False})


def _reformat_rule(**overrides) -> SignalRule:
    overrides.setdefault("kind", SignalKind.WHOLESALE_REFORMAT)
    overrides.setdefault("confidence", Confidence.MEDIUM)
    overrides.setdefault("is_active", True)
    return SignalRuleFactory(**overrides)


def _specs(*rules: SignalRule) -> list[DiffRuleSpec]:
    return [
        DiffRuleSpec(
            pk=rule.pk,
            kind=rule.kind,
            params=rule.effective_params(),
            tool=rule.tool,
            confidence=rule.confidence,
        )
        for rule in rules
    ]


# -- reading the diff out of the clone ------------------------------------------------------------


def test_the_context_carries_the_changed_lines_and_both_line_counts(repository, diff_origin, clone):
    pr = _reformat_pr(repository, diff_origin)

    context, base, head = load_diff_context(
        PullRequest.objects.prefetch_related("files").get(pk=pr.pk), clone, max_files=50
    )

    assert head == diff_origin.reformat_sha
    assert base and base != head
    assert [f.path for f in context.files] == ["src/app.py"]
    assert len(context.files[0].added_lines) == 120
    assert len(context.files[0].removed_lines) == 120
    # 240 lines moved, and none of them changed anything but whitespace.
    assert context.totals.lines == 240
    assert context.totals.semantic_lines == 0


def test_the_context_loads_the_files_content_before_the_change_as_a_baseline(repository, diff_origin, clone):
    pr = _reformat_pr(repository, diff_origin)

    context, _base, _head = load_diff_context(
        PullRequest.objects.prefetch_related("files").get(pk=pr.pk), clone, max_files=50
    )

    assert len(context.files[0].base_lines) == 120


def test_a_new_file_has_no_baseline_and_that_is_not_an_error(repository, diff_origin, clone):
    pr = _pr(
        repository,
        diff_origin.duplicate_sha,
        diff_origin.duplicate_merged_at,
        {"src/a.py": False, "src/b.py": False, "src/c.py": False},
    )

    context, _base, _head = load_diff_context(
        PullRequest.objects.prefetch_related("files").get(pk=pr.pk), clone, max_files=50
    )

    assert {f.path for f in context.files} == {"src/a.py", "src/b.py", "src/c.py"}
    assert all(f.base_lines == () for f in context.files)
    assert all(len(f.added_lines) == len(_BLOCK) for f in context.files)


# -- the outcome ---------------------------------------------------------------------------------


def test_a_reformatting_pull_request_trips_wholesale_reformat_and_nothing_else(
    repository, diff_origin, clone
):
    pr = _reformat_pr(repository, diff_origin)
    rules = _specs(
        _reformat_rule(name="reformat"),
        SignalRuleFactory(name="dupes", kind=SignalKind.DUPLICATED_BLOCKS, confidence=Confidence.LOW),
        SignalRuleFactory(name="density", kind=SignalKind.COMMENT_DENSITY_OUTLIER, confidence=Confidence.LOW),
    )

    outcome = analyse_pull_request_diff(
        PullRequest.objects.prefetch_related("files").get(pk=pr.pk), clone, rules, max_files=50
    )

    assert outcome.status == DiffAnalysis.Status.OK
    assert [match.code for _pk, match in outcome.matches] == [SignalKind.WHOLESALE_REFORMAT]


def test_duplicated_blocks_is_found_across_the_files_of_one_change(repository, diff_origin, clone):
    pr = _pr(
        repository,
        diff_origin.duplicate_sha,
        diff_origin.duplicate_merged_at,
        {"src/a.py": False, "src/b.py": False, "src/c.py": False},
    )
    rules = _specs(
        SignalRuleFactory(name="dupes", kind=SignalKind.DUPLICATED_BLOCKS, confidence=Confidence.LOW)
    )

    outcome = analyse_pull_request_diff(
        PullRequest.objects.prefetch_related("files").get(pk=pr.pk), clone, rules, max_files=50
    )

    assert [match.code for _pk, match in outcome.matches] == [SignalKind.DUPLICATED_BLOCKS]


def test_a_new_dependency_nothing_uses_is_found_from_the_manifest_diff(repository, diff_origin, clone):
    pr = _pr(
        repository,
        diff_origin.dependency_sha,
        diff_origin.dependency_merged_at,
        {"requirements.txt": False, "src/views.py": False},
    )
    rules = _specs(
        SignalRuleFactory(name="deps", kind=SignalKind.UNUSED_NEW_DEPENDENCY, confidence=Confidence.MEDIUM)
    )

    outcome = analyse_pull_request_diff(
        PullRequest.objects.prefetch_related("files").get(pk=pr.pk), clone, rules, max_files=50
    )

    packages = [match.params["package"] for _pk, match in outcome.matches]
    assert packages == ["httpx"]


def test_facts_are_collected_even_when_no_rule_matches(repository, diff_origin, clone):
    """`DiffFacts` feeds the policy engine, and a policy check is switched on independently of any
    detection rule — so the facts must be there whether or not a rule fired."""
    pr = _reformat_pr(repository, diff_origin)

    outcome = analyse_pull_request_diff(
        PullRequest.objects.prefetch_related("files").get(pk=pr.pk), clone, [], max_files=50
    )

    assert outcome.matches == ()
    assert DiffFacts.from_dict(outcome.facts).test_lines_added == 0
    assert outcome.facts != {}


def test_a_repository_with_no_clone_produces_no_signals_and_no_error(repository, diff_origin):
    pr = _reformat_pr(repository, diff_origin)

    outcome = analyse_pull_request_diff(
        PullRequest.objects.prefetch_related("files").get(pk=pr.pk),
        None,
        _specs(_reformat_rule(name="reformat")),
        max_files=50,
    )

    assert outcome.status == DiffAnalysis.Status.NO_CLONE
    assert outcome.matches == ()
    assert outcome.error == ""


def test_a_rebase_merge_is_refused_the_same_way_churn_refuses_it(repository, diff_origin, clone):
    pr = _reformat_pr(repository, diff_origin)
    pr.merge_method = PullRequest.MergeMethod.REBASE
    pr.save(update_fields=["merge_method"])

    outcome = analyse_pull_request_diff(
        PullRequest.objects.prefetch_related("files").get(pk=pr.pk), clone, [], max_files=50
    )

    assert outcome.status == DiffAnalysis.Status.UNSUPPORTED_MERGE_METHOD


def test_a_change_with_too_many_files_is_not_analysed(repository, diff_origin, clone):
    pr = _pr(
        repository,
        diff_origin.duplicate_sha,
        diff_origin.duplicate_merged_at,
        {"src/a.py": False, "src/b.py": False, "src/c.py": False},
    )

    outcome = analyse_pull_request_diff(
        PullRequest.objects.prefetch_related("files").get(pk=pr.pk), clone, [], max_files=2
    )

    assert outcome.status == DiffAnalysis.Status.TOO_LARGE
    assert outcome.matches == ()


def test_a_missing_merge_commit_is_an_error_rather_than_a_crash(repository, diff_origin, clone):
    pr = _reformat_pr(repository, diff_origin)
    pr.merge_commit_sha = ""
    pr.save(update_fields=["merge_commit_sha"])

    outcome = analyse_pull_request_diff(
        PullRequest.objects.prefetch_related("files").get(pk=pr.pk), clone, [], max_files=50
    )

    assert outcome.status == DiffAnalysis.Status.ERROR
    assert "no_merge_commit" in outcome.error


# -- inside run_churn ----------------------------------------------------------------------------


def test_no_diff_analysis_runs_until_a_repository_is_opted_in(repository, diff_origin):
    _reformat_pr(repository, diff_origin)
    _reformat_rule(name="reformat")

    result = run_churn(window_days=21)

    assert result.diffs_analysed == 0
    assert not DiffAnalysis.objects.exists()
    assert not AISignal.objects.exists()


def test_an_opted_in_repository_gets_its_diffs_analysed_and_its_signals_written(repository, diff_origin):
    set_setting("DIFF_ANALYSIS_REPOSITORIES", [repository.full_name])
    pr = _reformat_pr(repository, diff_origin)
    rule = _reformat_rule(name="reformat")

    result = run_churn(window_days=21)

    assert result.diffs_analysed == 1
    assert result.diff_signals_created == 1
    analysis = DiffAnalysis.objects.get(pull_request=pr)
    assert analysis.status == DiffAnalysis.Status.OK
    assert analysis.head_sha == diff_origin.reformat_sha
    signal = AISignal.objects.get(pull_request=pr)
    assert signal.signal_rule_id == rule.pk
    assert signal.evidence_code == SignalKind.WHOLESALE_REFORMAT
    # A structural signal stores a code plus parameters, never a rendered sentence (CLAUDE.md).
    assert signal.evidence == ""
    assert signal.evidence_params["semantic_lines"] == 0


def test_a_star_entry_opts_in_every_repository(repository, diff_origin):
    set_setting("DIFF_ANALYSIS_REPOSITORIES", ["*"])
    _reformat_pr(repository, diff_origin)
    _reformat_rule(name="reformat")

    assert run_churn(window_days=21).diffs_analysed == 1


def test_no_diffs_switches_the_whole_half_off(repository, diff_origin):
    set_setting("DIFF_ANALYSIS_REPOSITORIES", ["*"])
    _reformat_pr(repository, diff_origin)
    _reformat_rule(name="reformat")

    result = run_churn(window_days=21, analyse_diffs=False)

    assert result.diffs_analysed == 0
    assert not DiffAnalysis.objects.exists()


def test_a_second_run_writes_nothing_new(repository, diff_origin):
    set_setting("DIFF_ANALYSIS_REPOSITORIES", ["*"])
    pr = _reformat_pr(repository, diff_origin)
    _reformat_rule(name="reformat")
    run_churn(window_days=21)
    signal_ids = set(AISignal.objects.values_list("pk", flat=True))

    second = run_churn(window_days=21)

    assert second.diff_signals_created == 0
    assert second.diff_signals_deleted == 0
    # And the analysis settles, so the pull request is not re-read every night.
    assert second.diffs_analysed == 0
    assert set(AISignal.objects.values_list("pk", flat=True)) == signal_ids
    assert DiffAnalysis.objects.filter(pull_request=pr).count() == 1


def test_changing_a_threshold_retires_the_old_signal_and_writes_a_new_one(repository, diff_origin):
    set_setting("DIFF_ANALYSIS_REPOSITORIES", ["*"])
    pr = _reformat_pr(repository, diff_origin)
    rule = _reformat_rule(name="reformat")
    run_churn(window_days=21)
    first = AISignal.objects.get(pull_request=pr)

    rule.params = {"min_lines": 100}
    rule.save(update_fields=["params"])
    DiffAnalysis.objects.filter(pull_request=pr).delete()  # the nightly job re-reads it
    result = run_churn(window_days=21)

    assert result.diff_signals_created == 1
    assert result.diff_signals_deleted == 1
    current = AISignal.objects.get(pull_request=pr)
    assert current.pk != first.pk
    assert current.evidence_params["min_lines"] == 100


def test_deactivating_a_rule_removes_its_signals_on_the_next_run(repository, diff_origin):
    set_setting("DIFF_ANALYSIS_REPOSITORIES", ["*"])
    pr = _reformat_pr(repository, diff_origin)
    rule = _reformat_rule(name="reformat")
    run_churn(window_days=21)
    assert AISignal.objects.filter(pull_request=pr).exists()

    rule.is_active = False
    rule.save(update_fields=["is_active"])
    DiffAnalysis.objects.filter(pull_request=pr).delete()
    run_churn(window_days=21)

    assert not AISignal.objects.filter(pull_request=pr).exists()


def test_an_unreadable_diff_leaves_the_stored_signals_alone(repository, diff_origin):
    """A `no_clone` or `error` outcome says nothing about whether a signal still holds. Deleting
    the stored ones would let an unreachable repository erase its own evidence."""
    set_setting("DIFF_ANALYSIS_REPOSITORIES", ["*"])
    pr = _reformat_pr(repository, diff_origin)
    _reformat_rule(name="reformat")
    run_churn(window_days=21)
    signal_ids = set(AISignal.objects.values_list("pk", flat=True))

    DiffAnalysis.objects.filter(pull_request=pr).update(status=DiffAnalysis.Status.ERROR)
    pr.merge_commit_sha = "0" * 40
    pr.save(update_fields=["merge_commit_sha"])
    run_churn(window_days=21)

    assert DiffAnalysis.objects.get(pull_request=pr).status == DiffAnalysis.Status.ERROR
    assert set(AISignal.objects.values_list("pk", flat=True)) == signal_ids


def test_the_ai_status_moves_only_once_two_distinct_kinds_are_found(repository, diff_origin):
    """One structural signal is evidence a lead reads; two distinct kinds are what move a status.
    `AI_SUSPECTED_MIN_STRUCTURAL_KINDS` is 2 by default."""
    set_setting("DIFF_ANALYSIS_REPOSITORIES", ["*"])
    pr = _pr(
        repository,
        diff_origin.duplicate_sha,
        diff_origin.duplicate_merged_at,
        {"src/a.py": False, "src/b.py": False, "src/c.py": False},
    )
    SignalRuleFactory(name="dupes", kind=SignalKind.DUPLICATED_BLOCKS, confidence=Confidence.LOW)

    run_churn(window_days=21)

    pr.refresh_from_db()
    assert AISignal.objects.filter(pull_request=pr).count() == 1
    assert pr.ai_status != "ai_suspected"


# -- the guarantees ------------------------------------------------------------------------------


def test_diff_analysis_makes_no_github_call(repository, diff_origin):
    """`conftest.py` fails the suite on any unmocked outbound request, so a run that completes
    here has made none — and the clone is a `file://` URL. Asserted explicitly all the same,
    because "zero API calls" is the reason this family lives in the churn job at all."""
    set_setting("DIFF_ANALYSIS_REPOSITORIES", ["*"])
    _reformat_pr(repository, diff_origin)
    _reformat_rule(name="reformat")

    assert run_churn(window_days=21).diffs_analysed == 1


def test_no_credential_reaches_a_git_call_made_for_diff_analysis(monkeypatch, repository, diff_origin):
    set_setting("DIFF_ANALYSIS_REPOSITORIES", ["*"])
    _reformat_pr(repository, diff_origin)
    _reformat_rule(name="reformat")

    argv_calls: list[list[str]] = []
    real_run = subprocess.run

    def spy(args, **kwargs):
        argv_calls.append(list(args))
        return real_run(args, **kwargs)

    monkeypatch.setattr("apps.churn.gitcmd.subprocess.run", spy)

    run_churn(window_days=21)

    diff_calls = [call for call in argv_calls if "diff" in call or "show" in call]
    assert diff_calls
    for call in argv_calls:
        assert FORBIDDEN not in " ".join(call)


def test_no_stored_diff_row_contains_a_rendered_sentence(repository, diff_origin):
    """The bilingual rule (CLAUDE.md): system-generated text is stored as a code plus parameters
    and rendered in the reader's language, so no English prose may reach the database."""
    set_setting("DIFF_ANALYSIS_REPOSITORIES", ["*"])
    _reformat_pr(repository, diff_origin)
    _reformat_rule(name="reformat")

    run_churn(window_days=21)

    for signal in AISignal.objects.filter(signal_rule__isnull=False):
        assert signal.evidence == ""
        assert " " not in signal.evidence_code
    for analysis in DiffAnalysis.objects.all():
        assert analysis.error == ""
