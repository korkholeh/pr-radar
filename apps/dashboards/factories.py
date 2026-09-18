import factory
from factory.django import DjangoModelFactory

from apps.accounts.factories import UserFactory
from apps.dashboards.models import ExportJob


class ExportJobFactory(DjangoModelFactory):
    class Meta:
        model = ExportJob

    user = factory.SubFactory(UserFactory)
    kind = ExportJob.Kind.TABLE_CSV
    params = factory.Dict({})
