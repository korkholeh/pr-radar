import factory
from factory.django import DjangoModelFactory

from apps.activity.factories import PullRequestFactory
from apps.churn.models import ChurnResult


class ChurnResultFactory(DjangoModelFactory):
    class Meta:
        model = ChurnResult

    pull_request = factory.SubFactory(PullRequestFactory)
    window_days = 21
    status = ChurnResult.Status.OK
