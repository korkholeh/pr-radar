import pytest

from apps.activity.models import AIDisclosure
from apps.ai_detection.models import Confidence, Tool
from apps.policy.factories import AIPolicyFactory
from apps.policy.rules import RULES
from apps.policy.tests.helpers import make_context

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("disclosure", [AIDisclosure.MISSING, AIDisclosure.AMBIGUOUS])
def test_disclosure_missing_fires(disclosure):
    policy = AIPolicyFactory(require_disclosure=True)
    ctx = make_context(policy=policy, ai_disclosure=disclosure)
    findings = list(RULES["DISCLOSURE_MISSING"](ctx))
    assert len(findings) == 1
    assert findings[0].rule_code == "DISCLOSURE_MISSING"


def test_disclosure_missing_does_not_fire_for_partial():
    policy = AIPolicyFactory(require_disclosure=True)
    ctx = make_context(policy=policy, ai_disclosure=AIDisclosure.PARTIAL)
    assert list(RULES["DISCLOSURE_MISSING"](ctx)) == []


def test_disclosure_missing_does_not_fire_when_not_required():
    policy = AIPolicyFactory(require_disclosure=False)
    ctx = make_context(policy=policy, ai_disclosure=AIDisclosure.MISSING)
    assert list(RULES["DISCLOSURE_MISSING"](ctx)) == []


def test_disclosure_mismatch_fires_on_none_with_high_signal():
    ctx = make_context(ai_disclosure=AIDisclosure.NONE, signal_confidences={Confidence.HIGH})
    findings = list(RULES["DISCLOSURE_MISMATCH"](ctx))
    assert len(findings) == 1
    assert findings[0].rule_code == "DISCLOSURE_MISMATCH"


def test_disclosure_mismatch_does_not_fire_with_medium_signal():
    ctx = make_context(ai_disclosure=AIDisclosure.NONE, signal_confidences={Confidence.MEDIUM})
    assert list(RULES["DISCLOSURE_MISMATCH"](ctx)) == []


def test_disclosure_mismatch_does_not_fire_when_disclosed():
    ctx = make_context(ai_disclosure=AIDisclosure.PARTIAL, signal_confidences={Confidence.HIGH})
    assert list(RULES["DISCLOSURE_MISMATCH"](ctx)) == []


def test_tool_not_allowed_fires_one_finding_per_offending_tool():
    policy = AIPolicyFactory(allowed_tools=["copilot"])
    ctx = make_context(ai_tools=["cursor", "copilot"], policy=policy)
    findings = list(RULES["TOOL_NOT_ALLOWED"](ctx))
    assert [f.details_params["tool"] for f in findings] == ["cursor"]


def test_tool_not_allowed_does_not_fire_for_allowed_tool():
    policy = AIPolicyFactory(allowed_tools=["copilot"])
    ctx = make_context(ai_tools=["copilot"], policy=policy)
    findings = list(RULES["TOOL_NOT_ALLOWED"](ctx))
    assert findings == []


def test_tool_not_allowed_fires_nothing_when_allowed_tools_empty():
    policy = AIPolicyFactory(allowed_tools=[])
    ctx = make_context(ai_tools=["cursor"], policy=policy)
    findings = list(RULES["TOOL_NOT_ALLOWED"](ctx))
    assert findings == []


def test_tool_not_allowed_ignores_a_detected_other():
    """Behavioural signals (commit burst, mass file creation) file under `other` because they name
    no tool; that must not read as "tool Other is not allowed"."""
    policy = AIPolicyFactory(allowed_tools=["copilot"])
    ctx = make_context(
        ai_tools=[Tool.OTHER], signal_tools={Tool.OTHER}, declared_tools=frozenset(), policy=policy
    )
    assert list(RULES["TOOL_NOT_ALLOWED"](ctx)) == []


def test_tool_not_allowed_still_fires_for_a_declared_other():
    policy = AIPolicyFactory(allowed_tools=["copilot"])
    ctx = make_context(
        ai_tools=[Tool.OTHER],
        signal_tools={Tool.OTHER},
        declared_tools=frozenset({Tool.OTHER}),
        policy=policy,
    )
    findings = list(RULES["TOOL_NOT_ALLOWED"](ctx))
    assert [f.details_params for f in findings] == [{"tool": "other", "source": "declared"}]


@pytest.mark.parametrize(
    "declared,detected,source",
    [
        ({"cursor"}, set(), "declared"),
        (set(), {"cursor"}, "detected"),
        ({"cursor"}, {"cursor"}, "declared_and_detected"),
    ],
)
def test_tool_not_allowed_records_where_the_tool_came_from(declared, detected, source):
    policy = AIPolicyFactory(allowed_tools=["copilot"])
    ctx = make_context(
        ai_tools=sorted(declared | detected),
        signal_tools=detected,
        declared_tools=frozenset(declared),
        policy=policy,
    )
    findings = list(RULES["TOOL_NOT_ALLOWED"](ctx))
    assert [f.details_params for f in findings] == [{"tool": "cursor", "source": source}]
    assert [f.identity_params for f in findings] == [{"tool": "cursor"}]
