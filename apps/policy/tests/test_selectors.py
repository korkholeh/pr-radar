import datetime

import pytest
from django.utils import timezone
from freezegun import freeze_time

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory
from apps.activity.models import AIStatus
from apps.catalog.factories import IdentityFactory, PersonFactory, ProjectFactory, RepositoryFactory
from apps.policy.factories import PolicyViolationFactory
from apps.policy.models import PolicyViolation
from apps.policy.selectors import (
    compliance_kpis,
    disclosure_mismatch_pull_requests,
    projects_in_scope,
    repositories_in_scope,
    violations_by_rule,
    violations_for_pull_request,
    violations_in_scope,
)

pytestmark = pytest.mark.django_db

RuleCode = PolicyViolation.RuleCode


def _scope_for(*projects):
    return ScopeFilter(unrestricted=False, project_ids=frozenset(p.pk for p in projects))


def _pr_in_project(project):
    repository = RepositoryFactory()
    project.repositories.add(repository)
    return PullRequestFactory(repository=repository)


@freeze_time("2026-06-15T12:00:00Z")
def test_restricted_scope_hides_another_projects_violation_series_and_kpis():
    visible_project = ProjectFactory()
    hidden_project = ProjectFactory()
    visible_pr = _pr_in_project(visible_project)
    hidden_pr = _pr_in_project(hidden_project)
    PolicyViolationFactory(pull_request=visible_pr, rule_code=RuleCode.NO_TESTS)
    PolicyViolationFactory(pull_request=hidden_pr, rule_code=RuleCode.NO_TESTS)

    scope = _scope_for(visible_project)

    assert violations_in_scope(scope).count() == 1
    assert violations_in_scope(scope).first().pull_request_id == visible_pr.pk

    today = timezone.now().date()
    series = violations_by_rule(scope, today, today)
    assert sum(row.count for row in series) == 1

    kpis = compliance_kpis(scope, today, today)
    assert kpis.violations_open == 1


def test_violations_for_pull_request_is_scoped():
    project = ProjectFactory()
    other_project = ProjectFactory()
    pr = _pr_in_project(project)
    other_pr = _pr_in_project(other_project)
    PolicyViolationFactory(pull_request=pr)
    PolicyViolationFactory(pull_request=other_pr)

    scope = _scope_for(project)
    assert violations_for_pull_request(scope, other_pr.pk).count() == 0
    assert violations_for_pull_request(scope, pr.pk).count() == 1


@freeze_time("2026-06-15T12:00:00Z")
def test_violations_new_counts_inside_kyiv_period_and_excludes_one_second_before():
    pr = PullRequestFactory()
    period_day = datetime.date(2026, 6, 15)
    from apps.metrics.timeframe import day_start

    inside = PolicyViolationFactory(pull_request=pr, rule_code=RuleCode.NO_TESTS)
    inside.created_at = day_start(period_day)
    inside.save(update_fields=["created_at"])

    just_before = PolicyViolationFactory(pull_request=pr, rule_code=RuleCode.SELF_MERGE)
    just_before.created_at = day_start(period_day) - datetime.timedelta(seconds=1)
    just_before.save(update_fields=["created_at"])

    kpis = compliance_kpis(ScopeFilter(unrestricted=True), period_day, period_day)
    assert kpis.violations_new == 1


@freeze_time("2026-06-15T12:00:00Z")
def test_compliance_rate_is_none_without_denominator():
    today = timezone.now().date()
    kpis = compliance_kpis(ScopeFilter(unrestricted=True), today, today)
    assert kpis.ai_pr_compliance_rate is None
    assert kpis.sample_size == 0


@freeze_time("2026-06-15T12:00:00Z")
def test_kpis_report_sample_size_and_compliance_rate():
    today = timezone.now().date()
    compliant_pr = PullRequestFactory(ai_status=AIStatus.AI_EXPLICIT)
    non_compliant_pr = PullRequestFactory(ai_status=AIStatus.AI_EXPLICIT)
    PolicyViolationFactory(
        pull_request=non_compliant_pr, rule_code=RuleCode.NO_TESTS, status=PolicyViolation.Status.OPEN
    )

    kpis = compliance_kpis(ScopeFilter(unrestricted=True), today, today)
    assert kpis.sample_size == 2
    assert kpis.ai_pr_compliance_rate == pytest.approx(0.5)
    assert compliant_pr.ai_status == AIStatus.AI_EXPLICIT  # sanity: fixture is in the cohort


@freeze_time("2026-06-15T12:00:00Z")
def test_acknowledged_violation_does_not_count_as_open_or_non_compliant():
    today = timezone.now().date()
    pr = PullRequestFactory(ai_status=AIStatus.AI_EXPLICIT)
    PolicyViolationFactory(
        pull_request=pr, rule_code=RuleCode.NO_TESTS, status=PolicyViolation.Status.ACKNOWLEDGED
    )

    kpis = compliance_kpis(ScopeFilter(unrestricted=True), today, today)
    assert kpis.violations_open == 0
    assert kpis.ai_pr_compliance_rate == 1.0


@freeze_time("2026-06-15T12:00:00Z")
def test_bots_and_excluded_people_stay_out_of_the_ai_pr_denominator():
    today = timezone.now().date()
    bot_identity = IdentityFactory(person=PersonFactory(is_bot=True))
    excluded_identity = IdentityFactory(person=PersonFactory(exclude_from_metrics=True))
    PullRequestFactory(ai_status=AIStatus.AI_EXPLICIT, author=bot_identity)
    PullRequestFactory(ai_status=AIStatus.AI_EXPLICIT, author=excluded_identity)

    kpis = compliance_kpis(ScopeFilter(unrestricted=True), today, today)
    assert kpis.sample_size == 0
    assert kpis.ai_pr_compliance_rate is None


@freeze_time("2026-06-15T12:00:00Z")
def test_disclosure_mismatch_pull_requests_lists_only_open_mismatches():
    today = timezone.now().date()
    pr = PullRequestFactory()
    PolicyViolationFactory(
        pull_request=pr, rule_code=RuleCode.DISCLOSURE_MISMATCH, status=PolicyViolation.Status.OPEN
    )
    resolved_pr = PullRequestFactory()
    PolicyViolationFactory(
        pull_request=resolved_pr,
        rule_code=RuleCode.DISCLOSURE_MISMATCH,
        status=PolicyViolation.Status.RESOLVED,
    )

    result = disclosure_mismatch_pull_requests(ScopeFilter(unrestricted=True), today, today)
    assert list(result) == [pr]


def test_restricted_scope_hides_another_projects_filter_dropdown_entries():
    visible_project = ProjectFactory()
    hidden_project = ProjectFactory()
    visible_repository = RepositoryFactory()
    visible_project.repositories.add(visible_repository)
    hidden_repository = RepositoryFactory()
    hidden_project.repositories.add(hidden_repository)

    scope = _scope_for(visible_project)

    assert list(projects_in_scope(scope)) == [visible_project]
    assert list(repositories_in_scope(scope)) == [visible_repository]


def test_unrestricted_scope_lists_every_project_and_repository():
    project = ProjectFactory()
    repository = RepositoryFactory()
    project.repositories.add(repository)

    scope = ScopeFilter(unrestricted=True)

    assert project in projects_in_scope(scope)
    assert repository in repositories_in_scope(scope)


def test_filter_dropdowns_still_list_an_archived_project_and_repository():
    """Round-1 review: unifying with `catalog.selectors` must not silently drop archived rows from
    the Policy console's filter dropdowns — an existing violation on an archived project/repository
    still needs to be filterable to."""
    archived_project = ProjectFactory(is_active=False)
    archived_repository = RepositoryFactory(is_active=False)

    scope = ScopeFilter(unrestricted=True)

    assert archived_project in projects_in_scope(scope)
    assert archived_repository in repositories_in_scope(scope)
