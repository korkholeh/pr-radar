import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.selectors import ScopeFilter, scope_for_user
from apps.activity.factories import PullRequestFactory
from apps.catalog.factories import IdentityFactory, PersonFactory, ProjectFactory, RepositoryFactory
from apps.catalog.models import Identity
from apps.catalog.selectors import (
    people_for_settings,
    people_in_scope,
    projects_in_scope,
    repositories_in_scope,
    unmapped_identities,
    unmapped_identity_count,
)


@pytest.mark.django_db
def test_unmapped_identities_lists_only_unmapped_rows():
    mapped = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="mapped", person=PersonFactory())
    unmapped = IdentityFactory(kind=Identity.Kind.GIT_EMAIL, value="nobody@example.com")
    scope = scope_for_user(None)

    result = list(unmapped_identities(scope))

    assert unmapped in result
    assert mapped not in result


@pytest.mark.django_db
def test_unmapped_identity_count_matches_queryset():
    IdentityFactory(kind=Identity.Kind.GIT_EMAIL, value="a@example.com")
    IdentityFactory(kind=Identity.Kind.GIT_EMAIL, value="b@example.com")
    scope = scope_for_user(None)

    assert unmapped_identity_count(scope) == 2


@pytest.mark.django_db
def test_people_for_settings_query_count():
    for i in range(5):
        person = PersonFactory()
        IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value=f"login-{i}", person=person)

    scope = scope_for_user(None)
    with CaptureQueriesContext(connection) as ctx:
        for person in people_for_settings(scope):
            list(person.identities.all())

    assert len(ctx.captured_queries) <= 3


@pytest.mark.django_db
def test_projects_in_scope_excludes_inactive_and_narrows_by_scope():
    visible = ProjectFactory()
    other = ProjectFactory()
    inactive = ProjectFactory(is_active=False)

    unrestricted = list(projects_in_scope(ScopeFilter(unrestricted=True)))
    assert inactive not in unrestricted
    assert visible in unrestricted
    assert other in unrestricted

    restricted = list(projects_in_scope(ScopeFilter(unrestricted=False, project_ids=frozenset({visible.pk}))))
    assert restricted == [visible]


@pytest.mark.django_db
def test_projects_in_scope_include_inactive_keeps_archived_rows():
    """RISKS row 3 / round-1 review: `policy.selectors.projects_in_scope` delegates here with
    `include_inactive=True` so the Policy console's filter dropdown can still filter to a
    violation on an archived project — only the default (used by dashboards) excludes it."""
    active = ProjectFactory()
    inactive = ProjectFactory(is_active=False)

    default = list(projects_in_scope(ScopeFilter(unrestricted=True)))
    assert inactive not in default

    with_inactive = list(projects_in_scope(ScopeFilter(unrestricted=True), include_inactive=True))
    assert active in with_inactive
    assert inactive in with_inactive


@pytest.mark.django_db
def test_projects_in_scope_narrows_by_repository_ids():
    """Round 2 review BLOCKER fix: the dashboard filter bar's repository multi-select (spec
    §10.1) reuses `ScopeFilter.repository_ids`, independently of `project_ids`."""
    repo_in = RepositoryFactory()
    repo_out = RepositoryFactory()
    project_with_repo_in = ProjectFactory()
    project_with_repo_out = ProjectFactory()
    project_with_repo_in.repositories.add(repo_in)
    project_with_repo_out.repositories.add(repo_out)

    scope = ScopeFilter(unrestricted=False, repository_ids=frozenset({repo_in.pk}))
    result = list(projects_in_scope(scope))

    assert result == [project_with_repo_in]


@pytest.mark.django_db
def test_repositories_in_scope_narrows_by_repository_ids():
    repo_in = RepositoryFactory()
    repo_out = RepositoryFactory()

    scope = ScopeFilter(unrestricted=False, repository_ids=frozenset({repo_in.pk}))
    result = list(repositories_in_scope(scope))

    assert result == [repo_in]
    assert repo_out not in result


@pytest.mark.django_db
def test_repositories_in_scope_include_inactive_keeps_archived_rows():
    active = RepositoryFactory()
    inactive = RepositoryFactory(is_active=False)

    default = list(repositories_in_scope(ScopeFilter(unrestricted=True)))
    assert inactive not in default

    with_inactive = list(repositories_in_scope(ScopeFilter(unrestricted=True), include_inactive=True))
    assert active in with_inactive
    assert inactive in with_inactive


@pytest.mark.django_db
def test_repositories_in_scope_deduplicates_a_repository_shared_by_two_projects():
    shared_repo = RepositoryFactory()
    project_a = ProjectFactory()
    project_b = ProjectFactory()
    project_a.repositories.add(shared_repo)
    project_b.repositories.add(shared_repo)

    scope = ScopeFilter(unrestricted=False, project_ids=frozenset({project_a.pk, project_b.pk}))
    result = list(repositories_in_scope(scope))

    assert result == [shared_repo]


@pytest.mark.django_db
def test_repositories_in_scope_excludes_inactive():
    active = RepositoryFactory(is_active=True)
    inactive = RepositoryFactory(is_active=False)

    result = list(repositories_in_scope(ScopeFilter(unrestricted=True)))

    assert active in result
    assert inactive not in result


@pytest.mark.django_db
def test_people_in_scope_excludes_bots_and_excluded_people():
    normal = PersonFactory()
    bot = PersonFactory(is_bot=True)
    excluded = PersonFactory(exclude_from_metrics=True)
    inactive = PersonFactory(is_active=False)
    IdentityFactory(person=normal)
    IdentityFactory(person=bot)
    IdentityFactory(person=excluded)
    IdentityFactory(person=inactive)

    result = list(people_in_scope(ScopeFilter(unrestricted=True)))

    assert normal in result
    assert bot not in result
    assert excluded not in result
    assert inactive not in result


@pytest.mark.django_db
def test_people_in_scope_narrows_to_activity_in_the_callers_projects():
    repo = RepositoryFactory()
    project = ProjectFactory()
    project.repositories.add(repo)
    other_repo = RepositoryFactory()

    person = PersonFactory()
    identity = IdentityFactory(person=person)
    PullRequestFactory(repository=repo, author=identity)

    other_person = PersonFactory()
    other_identity = IdentityFactory(person=other_person)
    PullRequestFactory(repository=other_repo, author=other_identity)

    scope = ScopeFilter(unrestricted=False, project_ids=frozenset({project.pk}))
    result = list(people_in_scope(scope))

    assert person in result
    assert other_person not in result
