import factory
from django.conf import settings
from factory.django import DjangoModelFactory

from apps.accounts.models import AuditEntry, UserPreference, UserProjectAccess
from apps.catalog.factories import ProjectFactory


class UserFactory(DjangoModelFactory):
    class Meta:
        model = settings.AUTH_USER_MODEL
        django_get_or_create = ("username",)

    username = factory.Sequence(lambda n: f"user-{n}")


class UserPreferenceFactory(DjangoModelFactory):
    class Meta:
        model = UserPreference

    user = factory.SubFactory(UserFactory)


class AuditEntryFactory(DjangoModelFactory):
    class Meta:
        model = AuditEntry

    actor = factory.SubFactory(UserFactory)
    action = factory.Sequence(lambda n: f"action-{n}")
    object_type = "project"
    object_id = factory.Sequence(lambda n: str(n))
    changes = factory.Dict({"before": None, "after": None})


class UserProjectAccessFactory(DjangoModelFactory):
    class Meta:
        model = UserProjectAccess

    user = factory.SubFactory(UserFactory)
    project = factory.SubFactory(ProjectFactory)
