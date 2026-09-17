import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from apps.activity.factories import PullRequestFactory
from apps.catalog.factories import ProjectFactory
from apps.policy.models import AIPolicy, PolicyViolation, SensitivePathRule


class AIPolicyFactory(DjangoModelFactory):
    class Meta:
        model = AIPolicy

    effective_from = factory.LazyFunction(timezone.now)


class SensitivePathRuleFactory(DjangoModelFactory):
    class Meta:
        model = SensitivePathRule

    glob = factory.Sequence(lambda n: f"**/sensitive-{n}/**")
    ai_mode = SensitivePathRule.AiMode.FORBIDDEN


class SensitivePathRuleForProjectFactory(SensitivePathRuleFactory):
    project = factory.SubFactory(ProjectFactory)


class PolicyViolationFactory(DjangoModelFactory):
    class Meta:
        model = PolicyViolation

    pull_request = factory.SubFactory(PullRequestFactory)
    rule_code = PolicyViolation.RuleCode.NO_TESTS
    severity = PolicyViolation.Severity.LOW
    details_hash = factory.Sequence(lambda n: f"hash-{n}")
