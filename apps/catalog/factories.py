import factory
from factory.django import DjangoModelFactory

from apps.catalog.models import AppSetting, Identity, Organization, Person, Project, Repository
from apps.connections.factories import GitHubConnectionFactory


class OrganizationFactory(DjangoModelFactory):
    class Meta:
        model = Organization
        django_get_or_create = ("login",)

    login = factory.Sequence(lambda n: f"org-{n}")
    type = Organization.Type.ORG
    github_id = factory.Sequence(lambda n: f"O_{n}")


class RepositoryFactory(DjangoModelFactory):
    class Meta:
        model = Repository
        django_get_or_create = ("full_name",)

    organization = factory.SubFactory(OrganizationFactory)
    connection = factory.SubFactory(GitHubConnectionFactory)
    name = factory.Sequence(lambda n: f"repo-{n}")
    full_name = factory.LazyAttribute(lambda o: f"{o.organization.login}/{o.name}")
    github_id = factory.Sequence(lambda n: f"R_{n}")


class ProjectFactory(DjangoModelFactory):
    class Meta:
        model = Project
        django_get_or_create = ("slug",)

    name = factory.Sequence(lambda n: f"Project {n}")
    slug = factory.Sequence(lambda n: f"project-{n}")


class PersonFactory(DjangoModelFactory):
    class Meta:
        model = Person

    display_name = factory.Sequence(lambda n: f"Person {n}")


class IdentityFactory(DjangoModelFactory):
    class Meta:
        model = Identity
        django_get_or_create = ("kind", "value")

    kind = Identity.Kind.GITHUB_LOGIN
    value = factory.Sequence(lambda n: f"user-{n}")


class AppSettingFactory(DjangoModelFactory):
    class Meta:
        model = AppSetting
        django_get_or_create = ("key",)

    key = factory.Sequence(lambda n: f"SETTING_{n}")
    value_type = "str"
    value = "value"
