import factory
from factory.django import DjangoModelFactory

from apps.connections.models import GitHubConnection


class GitHubConnectionFactory(DjangoModelFactory):
    class Meta:
        model = GitHubConnection
        django_get_or_create = ("name",)

    name = factory.Sequence(lambda n: f"connection-{n}")
    kind = GitHubConnection.Kind.FINE_GRAINED_PAT
