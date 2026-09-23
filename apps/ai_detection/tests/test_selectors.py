import pytest

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory
from apps.activity.models import AIStatus
from apps.ai_detection.selectors import ai_cohort_pull_requests, recent_pull_requests
from apps.catalog.factories import PersonFactory, ProjectFactory, RepositoryFactory
from apps.catalog.models import Identity
from apps.catalog.services import set_setting


@pytest.fixture
def unrestricted_scope():
    return ScopeFilter(unrestricted=True, project_ids=None)


@pytest.mark.django_db
def test_cohort_is_ai_explicit_disclosed_and_suspected_by_default(unrestricted_scope):
    explicit = PullRequestFactory(ai_status=AIStatus.AI_EXPLICIT)
    disclosed = PullRequestFactory(ai_status=AIStatus.AI_DISCLOSED)
    suspected = PullRequestFactory(ai_status=AIStatus.AI_SUSPECTED)
    PullRequestFactory(ai_status=AIStatus.NO_AI)
    PullRequestFactory(ai_status=AIStatus.UNKNOWN)

    cohort_ids = set(ai_cohort_pull_requests(unrestricted_scope).values_list("pk", flat=True))

    assert cohort_ids == {explicit.pk, disclosed.pk, suspected.pk}


@pytest.mark.django_db
def test_cohort_loses_ai_suspected_when_setting_is_off(unrestricted_scope):
    set_setting("AI_COHORT_INCLUDE_SUSPECTED", False)
    suspected = PullRequestFactory(ai_status=AIStatus.AI_SUSPECTED)

    cohort_ids = set(ai_cohort_pull_requests(unrestricted_scope).values_list("pk", flat=True))

    assert suspected.pk not in cohort_ids


@pytest.mark.django_db
def test_bot_authored_ai_pr_is_not_in_the_cohort(unrestricted_scope):
    bot_person = PersonFactory(is_bot=True)
    bot_identity = Identity.objects.create(
        person=bot_person, kind=Identity.Kind.GITHUB_LOGIN, value="dependabot[bot]"
    )
    bot_pr = PullRequestFactory(ai_status=AIStatus.AI_EXPLICIT, author=bot_identity)

    cohort_ids = set(ai_cohort_pull_requests(unrestricted_scope).values_list("pk", flat=True))

    assert bot_pr.pk not in cohort_ids


@pytest.mark.django_db
def test_restricted_scope_hides_another_projects_pr():
    project = ProjectFactory()
    own_repository = RepositoryFactory()
    project.repositories.add(own_repository)
    other_repository = RepositoryFactory()

    own_pr = PullRequestFactory(repository=own_repository, ai_status=AIStatus.AI_EXPLICIT)
    other_pr = PullRequestFactory(repository=other_repository, ai_status=AIStatus.AI_EXPLICIT)

    scope = ScopeFilter(unrestricted=False, project_ids=frozenset({project.pk}))
    cohort_ids = set(ai_cohort_pull_requests(scope).values_list("pk", flat=True))

    assert own_pr.pk in cohort_ids
    assert other_pr.pk not in cohort_ids


@pytest.mark.django_db
def test_recent_pull_requests_respects_limit_and_orders_newest_first(unrestricted_scope):
    import datetime

    from django.utils import timezone

    now = timezone.now()
    older = PullRequestFactory(created_at=now - datetime.timedelta(days=2))
    newest = PullRequestFactory(created_at=now)
    middle = PullRequestFactory(created_at=now - datetime.timedelta(days=1))

    results = list(recent_pull_requests(unrestricted_scope, limit=2))

    assert [pr.pk for pr in results] == [newest.pk, middle.pk]
    assert older.pk not in [pr.pk for pr in results]
