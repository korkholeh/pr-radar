import pytest

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory
from apps.activity.models import AIStatus
from apps.activity.selectors import excluded_pull_requests
from apps.catalog.factories import IdentityFactory, PersonFactory, ProjectFactory
from apps.catalog.services import set_setting
from apps.metrics.models import Cohort, ScopeType
from apps.metrics.selectors import scoped_excluded_pull_requests, scoped_pull_requests
from apps.metrics.services import scope_for
from apps.metrics.types import Scope

pytestmark = pytest.mark.django_db

UNRESTRICTED = ScopeFilter(unrestricted=True, project_ids=None)


def _global_scope() -> Scope:
    return Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=UNRESTRICTED)


def test_bot_and_excluded_people_are_out_of_the_population_and_in_excluded():
    bot_person = PersonFactory(is_bot=True)
    bot_identity = IdentityFactory(person=bot_person)
    excluded_person = PersonFactory(exclude_from_metrics=True)
    excluded_identity = IdentityFactory(person=excluded_person)
    normal_person = PersonFactory()
    normal_identity = IdentityFactory(person=normal_person)

    bot_pr = PullRequestFactory(author=bot_identity)
    excluded_pr = PullRequestFactory(author=excluded_identity)
    normal_pr = PullRequestFactory(author=normal_identity)

    population = set(scoped_pull_requests(_global_scope()).values_list("id", flat=True))
    assert bot_pr.id not in population
    assert excluded_pr.id not in population
    assert normal_pr.id in population

    excluded = set(scoped_excluded_pull_requests(_global_scope()).values_list("id", flat=True))
    assert excluded == {bot_pr.id, excluded_pr.id}
    assert set(excluded_pull_requests(UNRESTRICTED).values_list("id", flat=True)) == {
        bot_pr.id,
        excluded_pr.id,
    }


def test_ai_cohort_is_explicit_disclosed_or_suspected_by_default():
    ai_explicit_pr = PullRequestFactory(ai_status=AIStatus.AI_EXPLICIT)
    ai_disclosed_pr = PullRequestFactory(ai_status=AIStatus.AI_DISCLOSED)
    ai_suspected_pr = PullRequestFactory(ai_status=AIStatus.AI_SUSPECTED)
    no_ai_pr = PullRequestFactory(ai_status=AIStatus.NO_AI)

    ai_ids = set(scoped_pull_requests(_global_scope(), cohort=Cohort.AI).values_list("id", flat=True))
    assert ai_ids == {ai_explicit_pr.id, ai_disclosed_pr.id, ai_suspected_pr.id}

    non_ai_ids = set(scoped_pull_requests(_global_scope(), cohort=Cohort.NON_AI).values_list("id", flat=True))
    assert non_ai_ids == {no_ai_pr.id}


def test_ai_cohort_loses_suspected_when_setting_is_off():
    set_setting("AI_COHORT_INCLUDE_SUSPECTED", False)
    ai_suspected_pr = PullRequestFactory(ai_status=AIStatus.AI_SUSPECTED)
    no_ai_pr = PullRequestFactory(ai_status=AIStatus.NO_AI)

    ai_ids = set(scoped_pull_requests(_global_scope(), cohort=Cohort.AI).values_list("id", flat=True))
    assert ai_suspected_pr.id not in ai_ids
    assert no_ai_pr.id not in ai_ids
    non_ai_ids = set(scoped_pull_requests(_global_scope(), cohort=Cohort.NON_AI).values_list("id", flat=True))
    assert ai_suspected_pr.id in non_ai_ids


def test_repo_in_two_projects_appears_once_at_project_scope():
    project_a = ProjectFactory()
    project_b = ProjectFactory()
    pr = PullRequestFactory()
    project_a.repositories.add(pr.repository)
    project_b.repositories.add(pr.repository)

    scope = Scope(scope_type=ScopeType.PROJECT, scope_id=project_a.id, access=UNRESTRICTED)
    matches = list(scoped_pull_requests(scope).values_list("id", flat=True))
    assert matches == [pr.id]


def test_scope_for_drops_an_out_of_scope_project_id(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="lead-user")
    accessible_project = ProjectFactory()
    other_project = ProjectFactory()
    restricted_access = ScopeFilter(unrestricted=False, project_ids=frozenset({accessible_project.id}))

    import apps.metrics.services as services_module

    monkeypatch.setattr(services_module, "scope_for_user", lambda _user: restricted_access)

    scope = scope_for(user, ScopeType.PROJECT, other_project.id)
    assert scope.scope_type == ScopeType.GLOBAL
    assert scope.scope_id is None

    scope = scope_for(user, ScopeType.PROJECT, accessible_project.id)
    assert scope.scope_type == ScopeType.PROJECT
    assert scope.scope_id == accessible_project.id
