"""The author-baseline structural kinds (phase 12, stage 5).

A per-PR kind in `structural.py` asks "is this pull request an odd shape?". A baseline kind asks
"is this author's recent work an odd shape *for them*?" — which is a different and, for this
product, a fairer question: a developer who has always shipped fast is not evidence of anything,
while a developer whose output changed shape last month might be.

Three consequences follow, and each is load-bearing:

**They run nightly, not during sync.** A baseline verdict changes when *other* pull requests
arrive, so computing it while syncing one pull request would freeze an answer that is already
going stale. `run_baselines()` recomputes the whole rolling window instead.

**They are normalised against the team.** A sprint, a release crunch or a quiet August moves
everybody at once. `throughput_shift` divides the author's change by the team's, so a tide that
lifts every boat lifts none of them into a signal.

**They refuse to speak from a thin sample.** Every kind needs at least `MIN_SAMPLE` pull requests
of history before it will emit anything, the same floor the metrics registry applies — a person
with four pull requests has no baseline, and inventing one for them is exactly the failure this
whole product is most at risk of (RISKS row 1: the tool measures people).

Like the per-PR kinds, none of these reads churn, reverts or follow-up fixes. Those are outcomes
measured *for* the AI cohort; feeding them back in would make the AI-quality dashboards prove
themselves (PLAN D7).
"""

from __future__ import annotations

import datetime
import statistics
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Q, Sum

from apps.activity.models import PullRequest, PullRequestCommit
from apps.ai_detection.evidence import EvidenceCode
from apps.ai_detection.models import SignalKind
from apps.catalog.services import get_int
from apps.metrics.timeframe import day_start, today

_DAYS_PER_WEEK = 7


@dataclass(frozen=True)
class BaselinePullRequest:
    id: int
    created_at: datetime.datetime
    effective_lines: int
    body_length: int
    test_lines: int
    impl_lines: int

    @property
    def test_ratio(self) -> float:
        return self.test_lines / self.impl_lines if self.impl_lines else 0.0


@dataclass(frozen=True)
class BaselineCommit:
    pull_request_id: int
    committed_at: datetime.datetime
    lines: int

    def local_hour(self) -> int:
        """The hour in `REPORT_TIMEZONE`, not UTC. "Outside their usual hours" is a statement
        about the person's day, and the person lives in a timezone."""
        return self.committed_at.astimezone(ZoneInfo(settings.REPORT_TIMEZONE)).hour


@dataclass(frozen=True)
class TeamBaseline:
    """What everyone else did over the same two windows, so a team-wide change cancels out."""

    earlier_pr_count: int
    recent_pr_count: int
    earlier_weeks: float
    recent_weeks: float

    @property
    def rate_ratio(self) -> float:
        """Recent pull requests per week over earlier pull requests per week, for the whole team.
        1.0 when the team is flat, 2.0 when the team as a whole doubled. Falls back to 1.0 rather
        than dividing by zero, which makes the normalisation a no-op instead of an error."""
        earlier_rate = self.earlier_pr_count / self.earlier_weeks if self.earlier_weeks else 0.0
        recent_rate = self.recent_pr_count / self.recent_weeks if self.recent_weeks else 0.0
        if earlier_rate <= 0:
            return 1.0
        return recent_rate / earlier_rate


@dataclass(frozen=True)
class AuthorBaseline:
    """One author's history, split into the window they are being judged on and the window they
    are being judged against."""

    person_id: int
    recent: tuple[BaselinePullRequest, ...]
    earlier: tuple[BaselinePullRequest, ...]
    recent_commits: tuple[BaselineCommit, ...]
    earlier_commits: tuple[BaselineCommit, ...]
    team: TeamBaseline
    recent_weeks: float
    earlier_weeks: float

    @property
    def all_pull_requests(self) -> tuple[BaselinePullRequest, ...]:
        return self.earlier + self.recent


@dataclass(frozen=True)
class BaselineMatch:
    pull_request_id: int
    code: str
    params: dict[str, Any]


BaselineFunction = Callable[[Mapping[str, Any], AuthorBaseline], Iterator[BaselineMatch]]


def _number(params: Mapping[str, Any], key: str, default: float) -> float:
    value = params.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return value


def _min_sample() -> int:
    return get_int("MIN_SAMPLE")


def _throughput_shift(params: Mapping[str, Any], author: AuthorBaseline) -> Iterator[BaselineMatch]:
    """This author's pull requests per week jumped against their own trailing rate — and against
    the team's, so a busy sprint that moved everybody moves nobody into a signal."""
    ratio_threshold = _number(params, "ratio", 2.5)
    if len(author.earlier) < _min_sample():
        return
    if not author.recent or not author.earlier_weeks or not author.recent_weeks:
        return

    earlier_rate = len(author.earlier) / author.earlier_weeks
    recent_rate = len(author.recent) / author.recent_weeks
    if earlier_rate <= 0:
        return

    author_ratio = recent_rate / earlier_rate
    team_ratio = max(author.team.rate_ratio, 1.0)  # a shrinking team must not inflate an author
    normalised = author_ratio / team_ratio
    if normalised < ratio_threshold:
        return

    for pull_request in author.recent:
        yield BaselineMatch(
            pull_request_id=pull_request.id,
            code=EvidenceCode.THROUGHPUT_SHIFT,
            params={
                "recent_per_week": round(recent_rate, 1),
                "earlier_per_week": round(earlier_rate, 1),
                "team_ratio": round(author.team.rate_ratio, 1),
                "ratio": round(normalised, 1),
                "min_ratio": ratio_threshold,
            },
        )


def _off_hours_volume(params: Mapping[str, Any], author: AuthorBaseline) -> Iterator[BaselineMatch]:
    """Most of this author's recent volume landed in hours they did not previously work in.

    "Usual hours" is the author's own: the smallest set of clock hours covering `percentile` of
    their earlier commits. Nothing here assumes a nine-to-five — a night owl's baseline is their
    own nights, and a change *away* from them is what fires.
    """
    percentile = _number(params, "percentile", 0.9)
    min_share = _number(params, "min_share", 0.4)
    if len(author.earlier) < _min_sample() or not author.earlier_commits or not author.recent_commits:
        return

    usual = _usual_hours(author.earlier_commits, percentile)
    if not usual or len(usual) >= 24:
        return

    recent_lines = sum(commit.lines for commit in author.recent_commits)
    if recent_lines <= 0:
        return
    off_hours_commits = [commit for commit in author.recent_commits if commit.local_hour() not in usual]
    off_hours_lines = sum(commit.lines for commit in off_hours_commits)
    share = off_hours_lines / recent_lines
    if share < min_share:
        return

    affected = {commit.pull_request_id for commit in off_hours_commits}
    for pull_request in author.recent:
        if pull_request.id not in affected:
            continue
        yield BaselineMatch(
            pull_request_id=pull_request.id,
            code=EvidenceCode.OFF_HOURS_VOLUME,
            params={
                "share": round(share * 100),
                "usual_hours": _format_hours(usual),
                "min_share": round(min_share * 100),
            },
        )


def _usual_hours(commits: Sequence[BaselineCommit], percentile: float) -> frozenset[int]:
    """The fewest clock hours that together account for `percentile` of the author's commits."""
    counts: dict[int, int] = {}
    for commit in commits:
        hour = commit.local_hour()
        counts[hour] = counts.get(hour, 0) + 1
    total = sum(counts.values())
    if total <= 0:
        return frozenset()

    target = total * min(max(percentile, 0.0), 1.0)
    running = 0
    chosen: set[int] = set()
    for hour, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        chosen.add(hour)
        running += count
        if running >= target:
            break
    return frozenset(chosen)


def _format_hours(hours: frozenset[int]) -> str:
    """A compact, language-neutral rendering: "09-12, 14, 21". Digits and punctuation only, so it
    can sit inside a translated sentence as a parameter without needing translation itself."""
    ordered = sorted(hours)
    spans: list[str] = []
    start = previous = ordered[0]
    for hour in ordered[1:]:
        if hour == previous + 1:
            previous = hour
            continue
        spans.append(f"{start:02d}" if start == previous else f"{start:02d}-{previous:02d}")
        start = previous = hour
    spans.append(f"{start:02d}" if start == previous else f"{start:02d}-{previous:02d}")
    return ", ".join(spans)


def _test_ratio_lockstep(params: Mapping[str, Any], author: AuthorBaseline) -> Iterator[BaselineMatch]:
    """The test-to-code ratio barely moves across many pull requests.

    Real work varies: a bug fix is mostly test, a migration mostly not. A ratio that holds to two
    decimal places across a dozen changes is a generator's habit, not a person's. Pull requests
    with no implementation lines are skipped rather than counted as zero — a docs-only change
    would otherwise drag the variance down and manufacture the signal.
    """
    min_prs = int(_number(params, "min_prs", 10))
    max_variance = _number(params, "max_variance", 0.15)
    floor = max(min_prs, _min_sample())

    considered = [pull_request for pull_request in author.all_pull_requests if pull_request.impl_lines > 0]
    if len(considered) < floor:
        return
    ratios = [pull_request.test_ratio for pull_request in considered]
    if all(ratio == 0 for ratio in ratios):
        return  # "never writes tests" is a policy question, not an authorship signal

    spread = statistics.pstdev(ratios)
    if spread > max_variance:
        return

    for pull_request in author.recent:
        if pull_request.impl_lines <= 0:
            continue
        yield BaselineMatch(
            pull_request_id=pull_request.id,
            code=EvidenceCode.TEST_RATIO_LOCKSTEP,
            params={
                "pr_count": len(considered),
                "spread": round(spread, 3),
                "mean_ratio": round(statistics.fmean(ratios), 2),
                "max_variance": max_variance,
            },
        )


def _body_style_shift(params: Mapping[str, Any], author: AuthorBaseline) -> Iterator[BaselineMatch]:
    """This author's pull-request descriptions changed length by a large factor, in either
    direction.

    Compared against the author's own median rather than any absolute idea of a good description,
    and measured in both directions on purpose: somebody who wrote two lines and now writes forty
    is as interesting as the reverse, and neither is an accusation on its own.
    """
    min_prs = int(_number(params, "min_prs", 10))
    length_ratio = _number(params, "length_ratio", 4)
    floor = max(min_prs, _min_sample())

    if len(author.earlier) < floor or not author.recent:
        return
    earlier_median = statistics.median(pr.body_length for pr in author.earlier)
    recent_median = statistics.median(pr.body_length for pr in author.recent)
    if earlier_median <= 0 or recent_median <= 0:
        return  # an empty-body history has no style to break

    ratio = max(recent_median / earlier_median, earlier_median / recent_median)
    if ratio < length_ratio:
        return

    for pull_request in author.recent:
        yield BaselineMatch(
            pull_request_id=pull_request.id,
            code=EvidenceCode.BODY_STYLE_SHIFT,
            params={
                "recent_length": int(recent_median),
                "earlier_length": int(earlier_median),
                "ratio": round(ratio, 1),
                "min_ratio": length_ratio,
            },
        )


BASELINE_FUNCTIONS: dict[str, BaselineFunction] = {
    SignalKind.THROUGHPUT_SHIFT: _throughput_shift,
    SignalKind.OFF_HOURS_VOLUME: _off_hours_volume,
    SignalKind.TEST_RATIO_LOCKSTEP: _test_ratio_lockstep,
    SignalKind.BODY_STYLE_SHIFT: _body_style_shift,
}


def run_baseline_kind(
    kind: str, params: Mapping[str, Any], author: AuthorBaseline
) -> Iterator[BaselineMatch]:
    function = BASELINE_FUNCTIONS.get(kind)
    if function is None:
        return iter(())
    return function(params, author)


# --- loading -------------------------------------------------------------------------------------


def _window_bounds(
    window_weeks: float, sustained_weeks: float, reference: datetime.date | None = None
) -> tuple[datetime.datetime, datetime.datetime, datetime.datetime]:
    """`(window_start, recent_start, window_end)` as UTC instants on `REPORT_TIMEZONE` day
    boundaries — the project's one rule for turning a calendar window into instants."""
    end_date = reference or today()
    window_end = day_start(end_date + datetime.timedelta(days=1))
    recent_start = day_start(end_date - datetime.timedelta(days=int(sustained_weeks * _DAYS_PER_WEEK) - 1))
    window_start = day_start(end_date - datetime.timedelta(days=int(window_weeks * _DAYS_PER_WEEK) - 1))
    return window_start, recent_start, window_end


def load_author_baselines(
    window_weeks: float = 8,
    sustained_weeks: float = 2,
    reference: datetime.date | None = None,
) -> list[AuthorBaseline]:
    """One pass over the window for every author with a resolved person, bounded by three queries
    plus one for the team totals — never one per author."""
    window_start, recent_start, window_end = _window_bounds(window_weeks, sustained_weeks, reference)

    rows = (
        PullRequest.objects.filter(
            created_at__gte=window_start,
            created_at__lt=window_end,
            author__person__isnull=False,
            author__person__is_bot=False,
        )
        .annotate(
            test_lines=Sum(
                "files__additions", filter=Q(files__is_test=True, files__is_excluded=False), default=0
            ),
            impl_lines=Sum(
                "files__additions", filter=Q(files__is_test=False, files__is_excluded=False), default=0
            ),
        )
        .values(
            "id",
            "created_at",
            "body",
            "effective_additions",
            "effective_deletions",
            "additions",
            "deletions",
            "test_lines",
            "impl_lines",
            "author__person_id",
        )
    )

    by_person: dict[int, list[BaselinePullRequest]] = {}
    pull_request_person: dict[int, int] = {}
    for row in rows:
        person_id = row["author__person_id"]
        additions = row["effective_additions"] if row["effective_additions"] is not None else row["additions"]
        deletions = row["effective_deletions"] if row["effective_deletions"] is not None else row["deletions"]
        by_person.setdefault(person_id, []).append(
            BaselinePullRequest(
                id=row["id"],
                created_at=row["created_at"],
                effective_lines=(additions or 0) + (deletions or 0),
                body_length=len(row["body"] or ""),
                test_lines=row["test_lines"] or 0,
                impl_lines=row["impl_lines"] or 0,
            )
        )
        pull_request_person[row["id"]] = person_id

    commits_by_person: dict[int, list[BaselineCommit]] = {}
    commit_rows = PullRequestCommit.objects.filter(
        pull_request_id__in=pull_request_person, commit__committed_at__isnull=False
    ).values(
        "pull_request_id",
        "commit__committed_at",
        "commit__additions",
        "commit__deletions",
    )
    for row in commit_rows:
        person_id = pull_request_person[row["pull_request_id"]]
        commits_by_person.setdefault(person_id, []).append(
            BaselineCommit(
                pull_request_id=row["pull_request_id"],
                committed_at=row["commit__committed_at"],
                lines=(row["commit__additions"] or 0) + (row["commit__deletions"] or 0),
            )
        )

    recent_weeks = sustained_weeks
    earlier_weeks = max(window_weeks - sustained_weeks, 0)
    team = TeamBaseline(
        earlier_pr_count=sum(
            1
            for pull_requests in by_person.values()
            for pull_request in pull_requests
            if pull_request.created_at < recent_start
        ),
        recent_pr_count=sum(
            1
            for pull_requests in by_person.values()
            for pull_request in pull_requests
            if pull_request.created_at >= recent_start
        ),
        earlier_weeks=earlier_weeks,
        recent_weeks=recent_weeks,
    )

    baselines: list[AuthorBaseline] = []
    for person_id, pull_requests in by_person.items():
        ordered = sorted(pull_requests, key=lambda item: item.created_at)
        commits = commits_by_person.get(person_id, [])
        recent_ids = {item.id for item in ordered if item.created_at >= recent_start}
        baselines.append(
            AuthorBaseline(
                person_id=person_id,
                recent=tuple(item for item in ordered if item.created_at >= recent_start),
                earlier=tuple(item for item in ordered if item.created_at < recent_start),
                recent_commits=tuple(commit for commit in commits if commit.pull_request_id in recent_ids),
                earlier_commits=tuple(
                    commit for commit in commits if commit.pull_request_id not in recent_ids
                ),
                team=team,
                recent_weeks=recent_weeks,
                earlier_weeks=earlier_weeks,
            )
        )
    return baselines
