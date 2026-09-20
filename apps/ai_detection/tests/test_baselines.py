"""The four author-baseline kinds and the nightly runner (phase 12, stage 5).

These kinds judge a person against their own history, which makes them the most consequential
heuristics in the product and the ones most in need of a firing case *and* a near-miss: the whole
point of a baseline is that steady work never fires, however fast or unusual it looks in absolute
terms.
"""

import datetime

import pytest
from django.core.management import call_command

from apps.activity.factories import CommitFactory, PRFileFactory, PullRequestCommitFactory, PullRequestFactory
from apps.activity.models import AIStatus, PullRequest
from apps.ai_detection.baselines import (
    BASELINE_FUNCTIONS,
    _format_hours,
    _usual_hours,
    load_author_baselines,
    run_baseline_kind,
)
from apps.ai_detection.evidence import EVIDENCE_MESSAGES
from apps.ai_detection.factories import SignalRuleFactory
from apps.ai_detection.models import (
    AISignal,
    Confidence,
    SignalFamily,
    SignalKind,
    kinds_in_family,
)
from apps.ai_detection.services import detect_pull_request, run_baselines
from apps.catalog.factories import IdentityFactory, PersonFactory, RepositoryFactory
from apps.catalog.services import set_setting
from apps.metrics.timeframe import day_start, today

# The default window is 8 weeks with the last 2 as "recent", so anything older than 14 days is
# history and anything newer is what the author is being judged on.
RECENT_DAYS = 10
EARLIER_DAYS = 40


@pytest.fixture
def repository(db):
    return RepositoryFactory()


def _author(name="dev"):
    person = PersonFactory(display_name=name)
    return IdentityFactory(person=person), person


def _pull_request(repository, identity, days_ago, *, body="fix", hour=14, lines=100, commits=1):
    created = day_start(today() - datetime.timedelta(days=days_ago)) + datetime.timedelta(hours=hour)
    pull_request = PullRequestFactory(
        repository=repository,
        author=identity,
        created_at=created,
        body=body,
        additions=lines,
        deletions=0,
        changed_files=2,
        state=PullRequest.State.MERGED,
    )
    for position in range(commits):
        commit = CommitFactory(
            repository=repository,
            committed_at=created + datetime.timedelta(minutes=position),
            additions=max(lines // max(commits, 1), 1),
            deletions=0,
        )
        PullRequestCommitFactory(pull_request=pull_request, commit=commit, position=position)
    return pull_request


def _baseline_for(person, **kwargs):
    return {item.person_id: item for item in load_author_baselines(**kwargs)}[person.pk]


def test_every_baseline_kind_has_a_function_and_an_evidence_message():
    baseline_kinds = kinds_in_family(SignalFamily.BASELINE)
    assert set(BASELINE_FUNCTIONS) == baseline_kinds
    for kind in baseline_kinds:
        assert kind in EVIDENCE_MESSAGES, kind


def test_the_three_families_do_not_overlap_and_cover_every_kind():
    per_pr = kinds_in_family(SignalFamily.PER_PR)
    baseline = kinds_in_family(SignalFamily.BASELINE)
    diff = kinds_in_family(SignalFamily.DIFF)
    assert not (per_pr & baseline)
    assert not (per_pr & diff)
    assert not (baseline & diff)
    # A kind in no family would be written by no pass and deleted by none either — it has to
    # belong to exactly one writer.
    assert per_pr | baseline | diff == set(SignalKind.values)


# -- the MIN_SAMPLE floor ------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("kind", sorted(kinds_in_family(SignalFamily.BASELINE)))
def test_an_author_below_min_sample_produces_no_baseline_signal(repository, kind):
    """A person with four pull requests has no baseline. Inventing one for them is the failure
    this product is most at risk of (RISKS row 1: the tool measures people)."""
    identity, person = _author()
    for index in range(3):
        _pull_request(repository, identity, EARLIER_DAYS + index)
    for index in range(6):
        _pull_request(repository, identity, index)

    assert list(run_baseline_kind(kind, {}, _baseline_for(person))) == []


# -- throughput_shift ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_team_wide_rise_produces_no_throughput_signal(repository):
    """A sprint, a release crunch or a return from holidays moves everybody at once. A tide that
    lifts every boat must lift none of them into a signal."""
    people = []
    for index in range(4):
        identity, person = _author(f"dev-{index}")
        people.append(person)
        for step in range(6):
            _pull_request(repository, identity, 21 + step * 4)
        for step in range(12):
            _pull_request(repository, identity, step)

    baseline = _baseline_for(people[0])
    assert baseline.team.rate_ratio > 2
    assert list(run_baseline_kind(SignalKind.THROUGHPUT_SHIFT, {}, baseline)) == []


@pytest.mark.django_db
def test_one_author_rising_against_a_flat_team_does_produce_a_signal(repository):
    identity, person = _author("busy")
    for step in range(6):
        _pull_request(repository, identity, 21 + step * 4)
    recent = [_pull_request(repository, identity, step) for step in range(12)]
    for index in range(4):
        steady_identity, _ = _author(f"steady-{index}")
        for step in range(6):
            _pull_request(repository, steady_identity, 21 + step * 4)
        for step in range(2):
            _pull_request(repository, steady_identity, step * 5)

    matches = list(run_baseline_kind(SignalKind.THROUGHPUT_SHIFT, {}, _baseline_for(person)))

    assert {match.pull_request_id for match in matches} == {item.pk for item in recent}
    params = matches[0].params
    assert params["recent_per_week"] > params["earlier_per_week"]
    assert params["team_ratio"] > 0  # the normalisation is shown, not hidden


# -- off_hours_volume ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_work_moving_outside_the_authors_own_hours_fires(repository):
    identity, person = _author("owl")
    for step in range(8):
        _pull_request(repository, identity, 21 + step * 3, hour=10, commits=2)
    for step in range(6):
        _pull_request(repository, identity, step * 2, hour=3, commits=2)

    matches = list(run_baseline_kind(SignalKind.OFF_HOURS_VOLUME, {}, _baseline_for(person)))

    assert matches
    assert matches[0].params["share"] == 100
    assert "10" in matches[0].params["usual_hours"]


@pytest.mark.django_db
def test_an_author_who_kept_their_hours_does_not_fire(repository):
    identity, person = _author("steady")
    for step in range(8):
        _pull_request(repository, identity, 21 + step * 3, hour=10, commits=2)
    for step in range(6):
        _pull_request(repository, identity, step * 2, hour=10, commits=2)

    assert list(run_baseline_kind(SignalKind.OFF_HOURS_VOLUME, {}, _baseline_for(person))) == []


@pytest.mark.django_db
def test_a_night_owls_baseline_is_their_own_nights(repository):
    """Nothing here assumes a nine-to-five: somebody who has always committed at 02:00 is not
    working "off hours"; a change *away* from their own pattern is what fires."""
    identity, person = _author("nocturnal")
    for step in range(8):
        _pull_request(repository, identity, 21 + step * 3, hour=2, commits=2)
    for step in range(6):
        _pull_request(repository, identity, step * 2, hour=2, commits=2)

    assert list(run_baseline_kind(SignalKind.OFF_HOURS_VOLUME, {}, _baseline_for(person))) == []


def test_usual_hours_picks_the_smallest_set_covering_the_percentile():
    from apps.ai_detection.baselines import BaselineCommit

    def commit(hour):
        return BaselineCommit(
            pull_request_id=1,
            committed_at=datetime.datetime(2026, 9, 1, hour, 0, tzinfo=datetime.UTC),
            lines=10,
        )

    # 9 commits at one local hour, 1 at another: the busy hour alone covers 90%.
    commits = [commit(7)] * 9 + [commit(20)]
    usual = _usual_hours(commits, 0.9)
    assert len(usual) == 1


def test_hours_render_as_compact_spans():
    assert _format_hours(frozenset({9, 10, 11, 14, 21})) == "09-11, 14, 21"


# -- test_ratio_lockstep -------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_ratio_that_never_moves_fires(repository):
    identity, person = _author("generator")
    for index in range(14):
        pull_request = _pull_request(repository, identity, 50 - index * 3)
        PRFileFactory(pull_request=pull_request, path="tests/test_a.py", additions=50, is_test=True)
        PRFileFactory(pull_request=pull_request, path="apps/a.py", additions=100, is_test=False)

    matches = list(run_baseline_kind(SignalKind.TEST_RATIO_LOCKSTEP, {}, _baseline_for(person)))

    assert matches
    assert matches[0].params["mean_ratio"] == 0.5


@pytest.mark.django_db
def test_a_ratio_that_varies_like_real_work_does_not_fire(repository):
    identity, person = _author("human")
    shapes = [(5, 100), (80, 20), (0, 60), (40, 40), (120, 30), (10, 200), (60, 60)]
    for index in range(14):
        test_lines, impl_lines = shapes[index % len(shapes)]
        pull_request = _pull_request(repository, identity, 50 - index * 3)
        PRFileFactory(pull_request=pull_request, path="tests/test_a.py", additions=test_lines, is_test=True)
        PRFileFactory(pull_request=pull_request, path="apps/a.py", additions=impl_lines, is_test=False)

    assert list(run_baseline_kind(SignalKind.TEST_RATIO_LOCKSTEP, {}, _baseline_for(person))) == []


@pytest.mark.django_db
def test_an_author_who_never_writes_tests_does_not_fire(repository):
    """A ratio pinned at zero is a policy question, not an authorship signal."""
    identity, person = _author("untested")
    for index in range(14):
        pull_request = _pull_request(repository, identity, 50 - index * 3)
        PRFileFactory(pull_request=pull_request, path="apps/a.py", additions=100, is_test=False)

    assert list(run_baseline_kind(SignalKind.TEST_RATIO_LOCKSTEP, {}, _baseline_for(person))) == []


# -- body_style_shift ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_descriptions_growing_by_a_large_factor_fires(repository):
    identity, person = _author("writer")
    for index in range(12):
        _pull_request(repository, identity, 52 - index * 3, body="fix")
    for index in range(4):
        _pull_request(repository, identity, index * 2, body="## Summary\n" + "x" * 800)

    matches = list(run_baseline_kind(SignalKind.BODY_STYLE_SHIFT, {}, _baseline_for(person)))

    assert matches
    assert matches[0].params["recent_length"] > matches[0].params["earlier_length"]


@pytest.mark.django_db
def test_descriptions_shrinking_by_a_large_factor_also_fires(repository):
    """Measured in both directions: somebody who wrote forty lines and now writes two is as
    interesting as the reverse, and neither is an accusation."""
    identity, person = _author("terse")
    for index in range(12):
        _pull_request(repository, identity, 52 - index * 3, body="x" * 800)
    for index in range(4):
        _pull_request(repository, identity, index * 2, body="fix")

    assert list(run_baseline_kind(SignalKind.BODY_STYLE_SHIFT, {}, _baseline_for(person)))


@pytest.mark.django_db
def test_a_steady_description_style_does_not_fire(repository):
    identity, person = _author("consistent")
    for index in range(12):
        _pull_request(repository, identity, 52 - index * 3, body="a normal description")
    for index in range(4):
        _pull_request(repository, identity, index * 2, body="another normal description")

    assert list(run_baseline_kind(SignalKind.BODY_STYLE_SHIFT, {}, _baseline_for(person))) == []


# -- the nightly runner --------------------------------------------------------------------------


def _seed_rising_author(repository):
    identity, person = _author("busy")
    for step in range(6):
        _pull_request(repository, identity, 21 + step * 4)
    recent = [_pull_request(repository, identity, step) for step in range(12)]
    for index in range(4):
        steady_identity, _ = _author(f"steady-{index}")
        for step in range(6):
            _pull_request(repository, steady_identity, 21 + step * 4)
        for step in range(2):
            _pull_request(repository, steady_identity, step * 5)
    return person, recent


@pytest.mark.django_db
def test_the_runner_writes_a_code_and_parameters_not_a_sentence(repository):
    SignalRuleFactory(kind=SignalKind.THROUGHPUT_SHIFT, confidence=Confidence.LOW)
    _seed_rising_author(repository)

    result = run_baselines()

    assert result.created > 0
    signal = AISignal.objects.filter(signal_rule__kind=SignalKind.THROUGHPUT_SHIFT).first()
    assert signal.evidence == ""
    assert signal.evidence_code == "throughput_shift"
    assert signal.rule_id is None


@pytest.mark.django_db
def test_running_the_baselines_twice_writes_nothing_the_second_time(repository):
    SignalRuleFactory(kind=SignalKind.THROUGHPUT_SHIFT, confidence=Confidence.LOW)
    _seed_rising_author(repository)

    run_baselines()
    second = run_baselines()

    assert second.created == 0
    assert second.deleted == 0


@pytest.mark.django_db
def test_deactivating_a_baseline_rule_removes_its_signals(repository):
    rule = SignalRuleFactory(kind=SignalKind.THROUGHPUT_SHIFT, confidence=Confidence.LOW)
    _seed_rising_author(repository)
    run_baselines()
    assert AISignal.objects.filter(signal_rule=rule).exists()

    rule.is_active = False
    rule.save(update_fields=["is_active"])
    result = run_baselines()

    assert result.deleted > 0
    assert not AISignal.objects.filter(signal_rule=rule).exists()


@pytest.mark.django_db
def test_a_baseline_run_does_not_delete_the_per_pr_signals(repository):
    """The hazard the family split exists to prevent: three writers, each reconciling only its
    own family. Without it a nightly run would wipe everything the sync had just written."""
    SignalRuleFactory(name="baseline", kind=SignalKind.THROUGHPUT_SHIFT, confidence=Confidence.LOW)
    per_pr_rule = SignalRuleFactory(
        name="per-pr", kind=SignalKind.MASS_FILE_CREATION, confidence=Confidence.MEDIUM
    )
    _person, recent = _seed_rising_author(repository)
    target = recent[0]
    for index in range(12):
        PRFileFactory(pull_request=target, path=f"apps/m{index % 4}/f{index}.py", status="added")
    detect_pull_request(target.pk)
    assert AISignal.objects.filter(pull_request=target, signal_rule=per_pr_rule).count() == 1

    run_baselines()

    assert AISignal.objects.filter(pull_request=target, signal_rule=per_pr_rule).count() == 1


@pytest.mark.django_db
def test_detection_does_not_delete_the_baseline_signals(repository):
    """The same hazard from the other side: a sync must not wipe last night's baseline work."""
    baseline_rule = SignalRuleFactory(kind=SignalKind.THROUGHPUT_SHIFT, confidence=Confidence.LOW)
    _person, recent = _seed_rising_author(repository)
    run_baselines()
    target = recent[0]
    assert AISignal.objects.filter(pull_request=target, signal_rule=baseline_rule).count() == 1

    detect_pull_request(target.pk)

    assert AISignal.objects.filter(pull_request=target, signal_rule=baseline_rule).count() == 1


@pytest.mark.django_db
def test_one_baseline_kind_alone_leaves_the_ai_status_unchanged(repository):
    SignalRuleFactory(kind=SignalKind.THROUGHPUT_SHIFT, confidence=Confidence.LOW)
    _person, recent = _seed_rising_author(repository)

    run_baselines()

    recent[0].refresh_from_db()
    assert recent[0].ai_status == AIStatus.UNKNOWN


@pytest.mark.django_db
def test_a_baseline_kind_and_a_per_pr_kind_together_make_it_suspected(repository):
    SignalRuleFactory(name="baseline", kind=SignalKind.THROUGHPUT_SHIFT, confidence=Confidence.LOW)
    SignalRuleFactory(name="per-pr", kind=SignalKind.MASS_FILE_CREATION, confidence=Confidence.MEDIUM)
    _person, recent = _seed_rising_author(repository)
    target = recent[0]
    for index in range(12):
        PRFileFactory(pull_request=target, path=f"apps/m{index % 4}/f{index}.py", status="added")

    detect_pull_request(target.pk)
    run_baselines()

    target.refresh_from_db()
    assert target.ai_status == AIStatus.AI_SUSPECTED


@pytest.mark.django_db
def test_removing_the_last_baseline_signal_restores_the_ai_status(repository):
    rule = SignalRuleFactory(name="baseline", kind=SignalKind.THROUGHPUT_SHIFT, confidence=Confidence.LOW)
    SignalRuleFactory(name="per-pr", kind=SignalKind.MASS_FILE_CREATION, confidence=Confidence.MEDIUM)
    _person, recent = _seed_rising_author(repository)
    target = recent[0]
    for index in range(12):
        PRFileFactory(pull_request=target, path=f"apps/m{index % 4}/f{index}.py", status="added")
    detect_pull_request(target.pk)
    run_baselines()
    target.refresh_from_db()
    assert target.ai_status == AIStatus.AI_SUSPECTED

    rule.is_active = False
    rule.save(update_fields=["is_active"])
    run_baselines()

    target.refresh_from_db()
    assert target.ai_status == AIStatus.UNKNOWN


@pytest.mark.django_db
def test_no_baseline_signal_ever_reaches_ai_explicit(repository):
    for kind in sorted(kinds_in_family(SignalFamily.BASELINE)):
        SignalRuleFactory(name=f"rule-{kind}", kind=kind, confidence=Confidence.MEDIUM)
    _person, recent = _seed_rising_author(repository)

    run_baselines()

    for pull_request in PullRequest.objects.all():
        assert pull_request.ai_status != AIStatus.AI_EXPLICIT


@pytest.mark.django_db
def test_a_bot_never_gets_a_baseline(repository):
    """Bots are excluded from every person-level number; a baseline for one would be noise at
    best and would put a machine account in a table of people at worst."""
    person = PersonFactory(display_name="dependabot", is_bot=True)
    identity = IdentityFactory(person=person)
    for step in range(6):
        _pull_request(repository, identity, 21 + step * 4)
    for step in range(12):
        _pull_request(repository, identity, step)

    assert person.pk not in {item.person_id for item in load_author_baselines()}


@pytest.mark.django_db
def test_the_min_sample_floor_follows_the_setting(repository):
    set_setting("MIN_SAMPLE", 20)
    identity, person = _author()
    for step in range(6):
        _pull_request(repository, identity, 21 + step * 4)
    for step in range(12):
        _pull_request(repository, identity, step)

    assert list(run_baseline_kind(SignalKind.THROUGHPUT_SHIFT, {}, _baseline_for(person))) == []


@pytest.mark.django_db
def test_the_management_command_runs_the_same_function(repository):
    SignalRuleFactory(kind=SignalKind.THROUGHPUT_SHIFT, confidence=Confidence.LOW)
    _seed_rising_author(repository)

    call_command("compute_baselines")

    assert AISignal.objects.filter(signal_rule__kind=SignalKind.THROUGHPUT_SHIFT).exists()
