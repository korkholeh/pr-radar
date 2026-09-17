import hashlib

import factory
from factory.django import DjangoModelFactory

from apps.activity.factories import PullRequestFactory
from apps.ai_detection.models import AISignal, Confidence, DetectionRule, Detector, Tool


class DetectionRuleFactory(DjangoModelFactory):
    class Meta:
        model = DetectionRule

    name = factory.Sequence(lambda n: f"rule-{n}")
    detector = Detector.COMMIT_TRAILER
    pattern = "Co-Authored-By: Claude"
    tool = Tool.CLAUDE_CODE
    confidence = Confidence.HIGH


class AISignalFactory(DjangoModelFactory):
    class Meta:
        model = AISignal

    pull_request = factory.SubFactory(PullRequestFactory)
    rule = factory.SubFactory(DetectionRuleFactory)
    tool = Tool.CLAUDE_CODE
    confidence = Confidence.HIGH
    evidence = "Co-Authored-By: Claude <noreply@anthropic.com>"
    evidence_hash = factory.LazyAttribute(lambda o: hashlib.sha256(o.evidence.encode()).hexdigest())
