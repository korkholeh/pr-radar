"""Shared context builder for rule-evaluator unit tests: the evaluators in `apps.policy.rules`
read only a `PolicyContext`'s fields, never the database, so tests build one directly instead of
going through `load_context()` (which is covered separately in `test_rules_registry.py`)."""

from __future__ import annotations

from apps.activity.factories import PullRequestFactory
from apps.activity.models import AIStatus
from apps.policy.factories import AIPolicyFactory
from apps.policy.rules import PolicyContext


def make_context(
    *,
    pull_request=None,
    policy=None,
    files=(),
    reviews=(),
    signal_confidences=frozenset(),
    signal_tools=frozenset(),
    sensitive_rules=(),
    is_ai=True,
    human_approver_person_ids=frozenset(),
    no_tests_min_lines=20,
    paths_in_params=20,
    **pr_kwargs,
) -> PolicyContext:
    if pull_request is None:
        pr_kwargs.setdefault("ai_status", AIStatus.AI_EXPLICIT if is_ai else AIStatus.NO_AI)
        pull_request = PullRequestFactory(**pr_kwargs)
    if policy is None:
        policy = AIPolicyFactory()
    return PolicyContext(
        pull_request=pull_request,
        policy=policy,
        files=tuple(files),
        reviews=tuple(reviews),
        signal_confidences=frozenset(signal_confidences),
        signal_tools=frozenset(signal_tools),
        sensitive_rules=tuple(sensitive_rules),
        is_ai=is_ai,
        human_approver_person_ids=frozenset(human_approver_person_ids),
        no_tests_min_lines=no_tests_min_lines,
        paths_in_params=paths_in_params,
    )
