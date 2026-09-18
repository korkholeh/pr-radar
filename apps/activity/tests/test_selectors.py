import pytest

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory
from apps.activity.selectors import bot_pull_request_count, pull_requests_for_metrics
from apps.catalog.factories import IdentityFactory, PersonFactory, ProjectFactory, RepositoryFactory
from apps.catalog.models import Identity


@pytest.mark.django_db
def test_bot_pr_is_out_of_the_metric_population_and_in_the_bot_counter(rf):
    scope = ScopeFilter(unrestricted=True)
    bot_person = PersonFactory(is_bot=True)
    bot_identity = IdentityFactory(
        kind=Identity.Kind.GITHUB_LOGIN, value="dependabot[bot]", person=bot_person
    )
    bot_pr = PullRequestFactory(author=bot_identity)

    assert bot_pr not in pull_requests_for_metrics(scope)
    assert bot_pull_request_count(scope) == 1


@pytest.mark.django_db
def test_excluded_person_pr_is_out_but_not_counted_as_a_bot():
    scope = ScopeFilter(unrestricted=True)
    excluded_person = PersonFactory(is_bot=False, exclude_from_metrics=True)
    identity = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="ex-employee", person=excluded_person)
    pr = PullRequestFactory(author=identity)

    assert pr not in pull_requests_for_metrics(scope)
    assert bot_pull_request_count(scope) == 0


@pytest.mark.django_db
def test_unmapped_author_pr_stays_in_the_metric_population():
    scope = ScopeFilter(unrestricted=True)
    identity = IdentityFactory(kind=Identity.Kind.GIT_EMAIL, value="nobody@example.com")
    pr = PullRequestFactory(author=identity)

    assert pr in pull_requests_for_metrics(scope)


@pytest.mark.django_db
def test_restricted_scope_does_not_double_count_a_pr_whose_repo_is_in_two_projects():
    repository = RepositoryFactory()
    project_a = ProjectFactory()
    project_b = ProjectFactory()
    project_a.repositories.add(repository)
    project_b.repositories.add(repository)
    identity = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN)
    in_scope_pr = PullRequestFactory(repository=repository, author=identity)
    out_of_scope_pr = PullRequestFactory(author=identity)

    scope = ScopeFilter(unrestricted=False, project_ids=frozenset({project_a.pk, project_b.pk}))
    result = pull_requests_for_metrics(scope)

    assert result.count() == 1
    assert list(result) == [in_scope_pr]
    assert out_of_scope_pr not in result
