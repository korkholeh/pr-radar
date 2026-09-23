"""T7: `apps/dashboards/person.py::build_comparison()` — person vs primary project vs
organization, both baselines real medians/sums over raw rows (ADR 0007), never a median of
per-person medians (RISKS row 1)."""

from __future__ import annotations

import datetime

import pytest

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory
from apps.catalog.factories import IdentityFactory, PersonFactory, ProjectFactory, RepositoryFactory
from apps.dashboards.params import DashboardParams
from apps.dashboards.person import Deviation, _deviation, build_comparison
from apps.metrics.models import ScopeType
from apps.metrics.rollups import rebuild
from apps.metrics.services import bump_data_version
from apps.metrics.types import Scope

DATE_FROM = datetime.date(2026, 8, 1)
DATE_TO = datetime.date(2026, 8, 31)

_PARAMS = DashboardParams(
    mode="period",
    preset="custom",
    date_from=DATE_FROM,
    date_to=DATE_TO,
    day=DATE_TO,
    granularity="week",
    granularity_is_auto=False,
    cohort="all",
    project_ids=(),
    repository_ids=(),
    q="",
    sort="",
    page=1,
    table="",
)


def _merged_pr(repository, identity, day):
    return PullRequestFactory(
        repository=repository,
        author=identity,
        state="merged",
        created_at=datetime.datetime(2026, 8, day, tzinfo=datetime.UTC),
        merged_at=datetime.datetime(2026, 8, day, tzinfo=datetime.UTC),
        last_activity_at=datetime.datetime(2026, 8, day, tzinfo=datetime.UTC),
    )


@pytest.mark.django_db
def test_build_comparison_hand_computed_values_for_two_person_project():
    repository = RepositoryFactory()
    project = ProjectFactory()
    project.repositories.add(repository)

    ada = PersonFactory(display_name="Ada")
    ada_identity = IdentityFactory(person=ada)
    _merged_pr(repository, ada_identity, 5)
    _merged_pr(repository, ada_identity, 6)

    bob = PersonFactory(display_name="Bob")
    bob_identity = IdentityFactory(person=bob)
    _merged_pr(repository, bob_identity, 7)
    _merged_pr(repository, bob_identity, 8)
    _merged_pr(repository, bob_identity, 9)

    rebuild(DATE_FROM, DATE_TO)
    bump_data_version()

    access = ScopeFilter(unrestricted=True)
    scope = Scope(scope_type=ScopeType.PERSON, scope_id=ada.pk, access=access)

    rows = build_comparison(scope, _PARAMS, ada)
    by_metric = {row.metric: row for row in rows}

    prs_merged = by_metric["prs_merged"]
    assert prs_merged.person_value == 2
    assert prs_merged.person_sample == 2
    assert prs_merged.project_value == 5
    assert prs_merged.org_value == 5
    assert prs_merged.below_min_sample is True  # 2 < MIN_SAMPLE (5)
    assert prs_merged.previous_value is None  # nothing seeded in the previous period


@pytest.mark.django_db
def test_build_comparison_person_with_no_prs_is_none_not_zero():
    repository = RepositoryFactory()
    project = ProjectFactory()
    project.repositories.add(repository)
    idle = PersonFactory(display_name="Idle")
    IdentityFactory(person=idle)

    rebuild(DATE_FROM, DATE_TO)
    bump_data_version()

    access = ScopeFilter(unrestricted=True)
    scope = Scope(scope_type=ScopeType.PERSON, scope_id=idle.pk, access=access)

    rows = build_comparison(scope, _PARAMS, idle)
    by_metric = {row.metric: row for row in rows}

    prs_merged = by_metric["prs_merged"]
    assert prs_merged.person_value is None
    assert prs_merged.person_sample == 0
    assert prs_merged.project_value is None  # no primary project: authored nothing in scope


@pytest.mark.django_db
def test_build_comparison_excludes_reviewer_response_from_baselines():
    repository = RepositoryFactory()
    project = ProjectFactory()
    project.repositories.add(repository)
    ada = PersonFactory(display_name="Ada")
    identity = IdentityFactory(person=ada)
    _merged_pr(repository, identity, 5)

    rebuild(DATE_FROM, DATE_TO)
    bump_data_version()

    access = ScopeFilter(unrestricted=True)
    scope = Scope(scope_type=ScopeType.PERSON, scope_id=ada.pk, access=access)

    rows = build_comparison(scope, _PARAMS, ada)
    by_metric = {row.metric: row for row in rows}

    reviewer_response = by_metric["reviewer_response_p50"]
    assert reviewer_response.project_value is None
    assert reviewer_response.org_value is None
    # No baseline, so no deviation either.
    assert reviewer_response.project_deviation is None
    assert reviewer_response.org_deviation is None


def test_deviation_is_the_signed_gap_and_its_ratio_to_the_baseline():
    deviation = _deviation("lead_time_p50", 150.0, 100.0)

    assert deviation == Deviation(delta=50.0, delta_ratio=0.5)


def test_deviation_ratio_is_none_against_a_zero_baseline():
    deviation = _deviation("rework_rate", 0.2, 0.0)

    assert deviation == Deviation(delta=0.2, delta_ratio=None)


def test_deviation_is_none_for_a_counter_or_a_missing_value():
    # A counter's project/organization value is the whole level's total, not a typical person's.
    assert _deviation("prs_merged", 2, 5) is None
    assert _deviation("lead_time_p50", None, 100.0) is None
    assert _deviation("lead_time_p50", 100.0, None) is None


@pytest.mark.django_db
def test_build_comparison_counters_carry_no_deviation():
    repository = RepositoryFactory()
    project = ProjectFactory()
    project.repositories.add(repository)
    ada = PersonFactory(display_name="Ada")
    _merged_pr(repository, IdentityFactory(person=ada), 5)
    _merged_pr(repository, IdentityFactory(person=PersonFactory(display_name="Bob")), 6)

    rebuild(DATE_FROM, DATE_TO)
    bump_data_version()

    scope = Scope(scope_type=ScopeType.PERSON, scope_id=ada.pk, access=ScopeFilter(unrestricted=True))
    by_metric = {row.metric: row for row in build_comparison(scope, _PARAMS, ada)}

    assert by_metric["prs_merged"].person_value == 1
    assert by_metric["prs_merged"].org_value == 2
    assert by_metric["prs_merged"].org_deviation is None
    assert by_metric["prs_merged"].project_deviation is None
