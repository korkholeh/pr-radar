"""`apps/dashboards/person.py::build_violation_stats()` and its table on the person page:
violations on the person's pull requests recorded in the period, one row per rule, split by
current status."""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory
from apps.catalog.factories import IdentityFactory, PersonFactory, ProjectFactory, RepositoryFactory
from apps.dashboards.params import DashboardParams
from apps.dashboards.person import build_violation_stats
from apps.metrics.models import ScopeType
from apps.metrics.types import Scope
from apps.policy.factories import PolicyViolationFactory
from apps.policy.models import PolicyViolation

DATE_FROM = datetime.date(2026, 8, 1)
DATE_TO = datetime.date(2026, 8, 31)
PERIOD_QS = "preset=custom&from=2026-08-01&to=2026-08-31&granularity=week"

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

Rule = PolicyViolation.RuleCode
Severity = PolicyViolation.Severity
Status = PolicyViolation.Status


def _violation(pull_request, rule_code, severity, status):
    # Counted by the pull request's `created_at`; the violation's own (`auto_now_add`, today) is
    # when a sync wrote it and must not matter.
    return PolicyViolationFactory(
        pull_request=pull_request, rule_code=rule_code, severity=severity, status=status
    )


def _august(day):
    return datetime.datetime(2026, 8, day, 12, tzinfo=datetime.UTC)


def _seed():
    repository = RepositoryFactory()
    project = ProjectFactory()
    project.repositories.add(repository)
    person = PersonFactory(display_name="Ada Lovelace")
    other = PersonFactory(display_name="Grace Hopper")
    identity = IdentityFactory(person=person)
    pr = PullRequestFactory(repository=repository, author=identity, created_at=_august(5))
    second_pr = PullRequestFactory(repository=repository, author=identity, created_at=_august(7))
    july_pr = PullRequestFactory(
        repository=repository, author=identity, created_at=datetime.datetime(2026, 7, 15, tzinfo=datetime.UTC)
    )
    other_pr = PullRequestFactory(
        repository=repository, author=IdentityFactory(person=other), created_at=_august(10)
    )

    _violation(pr, Rule.NO_TESTS, Severity.LOW, Status.OPEN)
    _violation(pr, Rule.NO_TESTS, Severity.LOW, Status.OPEN)
    _violation(second_pr, Rule.NO_TESTS, Severity.LOW, Status.RESOLVED)
    _violation(pr, Rule.SELF_MERGE, Severity.HIGH, Status.WAIVED)
    _violation(pr, Rule.DISCLOSURE_MISSING, Severity.MEDIUM, Status.ACKNOWLEDGED)
    # On a pull request opened before the period: not counted.
    _violation(july_pr, Rule.SELF_MERGE, Severity.HIGH, Status.OPEN)
    # Someone else's pull request: not counted.
    _violation(other_pr, Rule.SELF_MERGE, Severity.HIGH, Status.OPEN)
    return person


def _person_scope(person):
    return Scope(ScopeType.PERSON, person.pk, ScopeFilter(unrestricted=True))


@pytest.mark.django_db
def test_build_violation_stats_counts_by_rule_and_status_in_severity_order():
    person = _seed()

    stats = build_violation_stats(_person_scope(person), _PARAMS)

    assert [(row.rule_code, row.total) for row in stats.rows] == [
        (Rule.SELF_MERGE, 1),
        (Rule.DISCLOSURE_MISSING, 1),
        (Rule.NO_TESTS, 3),
    ]
    no_tests = stats.rows[2]
    assert (no_tests.open, no_tests.acknowledged, no_tests.waived, no_tests.resolved) == (2, 0, 0, 1)
    assert stats.totals == {"total": 5, "open": 2, "acknowledged": 1, "waived": 1, "resolved": 1}
    # Distinct pull requests: both carry several violations.
    assert stats.pull_requests == 2


@pytest.mark.django_db
def test_build_violation_stats_is_empty_for_a_person_with_none():
    person = PersonFactory()

    stats = build_violation_stats(_person_scope(person), _PARAMS)

    assert stats.rows == []
    assert stats.totals["total"] == 0
    assert stats.pull_requests == 0


@pytest.mark.django_db
def test_person_page_renders_the_violations_table(client, lead_user):
    person = _seed()
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:person", args=[person.pk]) + f"?{PERIOD_QS}")

    body = response.content.decode()
    assert 'data-testid="person-violations"' in body
    assert body.count('data-testid="person-violation-row"') == 3
    assert str(Rule.SELF_MERGE.label) in body
    assert "Found on 2 pull requests in this period." in body


@pytest.mark.django_db
def test_person_page_shows_an_empty_state_without_violations(client, lead_user):
    person = PersonFactory()
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:person", args=[person.pk]) + f"?{PERIOD_QS}")

    body = response.content.decode()
    assert 'data-testid="person-violations"' in body
    assert 'data-testid="person-violation-row"' not in body
