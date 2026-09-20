"""Fixtures shared by the ai_detection test modules."""

import pytest

from apps.activity.factories import PullRequestFactory
from apps.ai_detection.factories import DetectionRuleFactory, SignalRuleFactory


@pytest.fixture
def pull_request(db):
    return PullRequestFactory()


@pytest.fixture
def detection_rule(db):
    return DetectionRuleFactory()


@pytest.fixture
def signal_rule(db):
    return SignalRuleFactory()
