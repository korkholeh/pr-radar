import pytest
from django.core.exceptions import ValidationError

from apps.activity.factories import CommitFactory, PullRequestFactory, ReviewCommentFactory, ReviewFactory
from apps.catalog.factories import IdentityFactory, PersonFactory
from apps.catalog.identity import (
    is_bot_login,
    login_from_noreply_email,
    merge_people,
    person_for_login,
    resolve_identities_for_pull_request,
    resolve_identity,
)
from apps.catalog.models import Identity
from apps.catalog.services import set_setting


@pytest.mark.django_db
class TestIsBotLogin:
    def test_suffix_match(self):
        assert is_bot_login("dependabot[bot]") is True

    def test_exact_match(self):
        assert is_bot_login("renovate") is True

    def test_case_insensitive(self):
        assert is_bot_login("Github-Actions") is True

    def test_unlisted_login_is_not_a_bot(self):
        assert is_bot_login("dependabot-preview") is False

    def test_configurable_via_app_setting(self):
        set_setting("BOT_LOGINS", ["special-bot"])
        assert is_bot_login("special-bot") is True
        assert is_bot_login("renovate") is False


class TestLoginFromNoreplyEmail:
    def test_id_plus_login(self):
        assert login_from_noreply_email("12345+octocat@users.noreply.github.com") == "octocat"

    def test_login_only(self):
        assert login_from_noreply_email("octocat@users.noreply.github.com") == "octocat"

    def test_non_noreply_domain_is_none(self):
        assert login_from_noreply_email("octocat@example.com") is None

    def test_empty_login_is_none(self):
        assert login_from_noreply_email("+@users.noreply.github.com") is None


@pytest.mark.django_db
class TestResolveIdentity:
    def test_new_login_identity_auto_creates_a_person(self):
        identity = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="octocat")

        resolve_identity(identity)

        identity.refresh_from_db()
        assert identity.person is not None
        assert identity.person.display_name == "octocat"
        assert identity.person.is_bot is False

    def test_bot_login_auto_creates_a_bot_person(self):
        identity = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="dependabot[bot]")

        resolve_identity(identity)

        identity.refresh_from_db()
        assert identity.person.is_bot is True

    def test_noreply_email_adopts_the_login_person(self):
        login_identity = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="octocat")
        person = person_for_login(login_identity)
        email_identity = IdentityFactory(
            kind=Identity.Kind.GIT_EMAIL, value="octocat@users.noreply.github.com"
        )

        resolve_identity(email_identity)

        email_identity.refresh_from_db()
        assert email_identity.person_id == person.id

    def test_github_linked_commit_email_adopts_the_login_person(self):
        login_identity = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="octocat")
        person = person_for_login(login_identity)
        email_identity = IdentityFactory(kind=Identity.Kind.GIT_EMAIL, value="octocat@example.com")
        CommitFactory(author_identity=login_identity, author_email_identity=email_identity)

        resolve_identity(email_identity)

        email_identity.refresh_from_db()
        assert email_identity.person_id == person.id

    def test_bare_email_stays_unmapped(self):
        identity = IdentityFactory(kind=Identity.Kind.GIT_EMAIL, value="nobody@example.com")

        resolve_identity(identity)

        identity.refresh_from_db()
        assert identity.person is None

    def test_already_mapped_identity_is_never_repointed(self):
        person = PersonFactory(display_name="Chosen by a lead")
        identity = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="octocat", person=person)

        resolve_identity(identity)

        identity.refresh_from_db()
        assert identity.person_id == person.id

    def test_resolving_twice_creates_no_second_person_or_identity(self):
        identity = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="octocat")

        resolve_identity(identity)
        resolve_identity(identity)

        assert Identity.objects.filter(value="octocat").count() == 1


@pytest.mark.django_db
class TestResolveIdentitiesForPullRequest:
    def test_resolves_author_reviewer_and_commenter(self):
        author = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="author")
        reviewer = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="reviewer")
        commenter = IdentityFactory(kind=Identity.Kind.GITHUB_LOGIN, value="commenter")
        pull_request = PullRequestFactory(author=author)
        ReviewFactory(pull_request=pull_request, reviewer=reviewer)
        ReviewCommentFactory(pull_request=pull_request, author=commenter)

        resolve_identities_for_pull_request(pull_request.id)

        for identity in (author, reviewer, commenter):
            identity.refresh_from_db()
            assert identity.person is not None


@pytest.mark.django_db
class TestMergePeople:
    def test_merge_refuses_self_merge(self):
        person = PersonFactory()
        with pytest.raises(ValidationError):
            merge_people(person, person, actor=None)
