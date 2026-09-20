import hashlib

import factory
from factory.django import DjangoModelFactory

from apps.activity.factories import PullRequestFactory
from apps.ai_detection.models import (
    AISignal,
    Confidence,
    DetectionRule,
    Detector,
    DiffAnalysis,
    SignalKind,
    SignalRule,
    Tool,
)


class DetectionRuleFactory(DjangoModelFactory):
    class Meta:
        model = DetectionRule

    name = factory.Sequence(lambda n: f"rule-{n}")
    detector = Detector.COMMIT_TRAILER
    pattern = "Co-Authored-By: Claude"
    tool = Tool.CLAUDE_CODE
    confidence = Confidence.HIGH


class SignalRuleFactory(DjangoModelFactory):
    """Never `high`: the database refuses it outright, so a factory defaulting to `high` would
    fail at insert time in every test that touched it."""

    class Meta:
        model = SignalRule

    name = factory.Sequence(lambda n: f"signal-rule-{n}")
    kind = SignalKind.COMMIT_BURST
    confidence = Confidence.MEDIUM
    tool = Tool.OTHER


class AISignalFactory(DjangoModelFactory):
    class Meta:
        model = AISignal

    pull_request = factory.SubFactory(PullRequestFactory)
    rule = factory.SubFactory(DetectionRuleFactory)
    tool = Tool.CLAUDE_CODE
    confidence = Confidence.HIGH
    evidence = "Co-Authored-By: Claude <noreply@anthropic.com>"
    evidence_hash = factory.LazyAttribute(lambda o: hashlib.sha256(o.evidence.encode()).hexdigest())


class DiffAnalysisFactory(DjangoModelFactory):
    """A settled, empty analysis by default: `status=ok` with no facts is what a pull request whose
    diff carried nothing interesting looks like."""

    class Meta:
        model = DiffAnalysis

    pull_request = factory.SubFactory(PullRequestFactory)
    status = DiffAnalysis.Status.OK
    facts = factory.LazyFunction(dict)
    base_sha = "0" * 40
    head_sha = "1" * 40
