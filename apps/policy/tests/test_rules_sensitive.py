import pytest

from apps.activity.factories import PRFileFactory, PullRequestFactory
from apps.catalog.factories import ProjectFactory
from apps.policy.factories import SensitivePathRuleFactory, SensitivePathRuleForProjectFactory
from apps.policy.models import SensitivePathRule
from apps.policy.rules import RULES
from apps.policy.services import match_sensitive_paths
from apps.policy.tests.helpers import make_context

pytestmark = pytest.mark.django_db


def test_global_rule_matches():
    rule = SensitivePathRuleFactory(glob="secrets/**", ai_mode=SensitivePathRule.AiMode.FORBIDDEN)
    pr = PullRequestFactory()
    pr_file = PRFileFactory(pull_request=pr, path="secrets/key.pem")
    match_sensitive_paths(pr, [rule])
    pr_file.refresh_from_db()
    assert pr_file.matched_sensitive_rule_id == rule.pk


def test_project_rule_matches_only_that_projects_repositories():
    project = ProjectFactory()
    rule = SensitivePathRuleForProjectFactory(
        project=project, glob="infra/**", ai_mode=SensitivePathRule.AiMode.FORBIDDEN
    )
    in_project_pr = PullRequestFactory()
    project.repositories.add(in_project_pr.repository)
    outside_pr = PullRequestFactory()

    in_project_file = PRFileFactory(pull_request=in_project_pr, path="infra/main.tf")
    outside_file = PRFileFactory(pull_request=outside_pr, path="infra/main.tf")

    match_sensitive_paths(in_project_pr, [rule])
    match_sensitive_paths(outside_pr, [rule])

    in_project_file.refresh_from_db()
    outside_file.refresh_from_db()
    assert in_project_file.matched_sensitive_rule_id == rule.pk
    assert outside_file.matched_sensitive_rule_id is None


def test_non_matching_path_does_not_match():
    rule = SensitivePathRuleFactory(glob="secrets/**", ai_mode=SensitivePathRule.AiMode.FORBIDDEN)
    pr = PullRequestFactory()
    pr_file = PRFileFactory(pull_request=pr, path="src/app.py")
    match_sensitive_paths(pr, [rule])
    pr_file.refresh_from_db()
    assert pr_file.matched_sensitive_rule_id is None


def test_excluded_file_does_not_match():
    rule = SensitivePathRuleFactory(glob="secrets/**", ai_mode=SensitivePathRule.AiMode.FORBIDDEN)
    pr = PullRequestFactory()
    pr_file = PRFileFactory(pull_request=pr, path="secrets/key.pem", is_excluded=True)
    match_sensitive_paths(pr, [rule])
    pr_file.refresh_from_db()
    assert pr_file.matched_sensitive_rule_id is None


def test_running_twice_is_idempotent_and_writes_no_further_updates(django_assert_num_queries):
    rule = SensitivePathRuleFactory(glob="secrets/**", ai_mode=SensitivePathRule.AiMode.FORBIDDEN)
    pr = PullRequestFactory()
    PRFileFactory(pull_request=pr, path="secrets/key.pem")
    match_sensitive_paths(pr, [rule])

    with django_assert_num_queries(2):  # project ids + files; no bulk_update UPDATE this time
        match_sensitive_paths(pr, [rule])


def test_deactivating_rule_clears_the_mark():
    rule = SensitivePathRuleFactory(glob="secrets/**", ai_mode=SensitivePathRule.AiMode.FORBIDDEN)
    pr = PullRequestFactory()
    pr_file = PRFileFactory(pull_request=pr, path="secrets/key.pem")
    match_sensitive_paths(pr, [rule])
    pr_file.refresh_from_db()
    assert pr_file.matched_sensitive_rule_id == rule.pk

    rule.is_active = False
    rule.save(update_fields=["is_active"])
    match_sensitive_paths(pr, [])  # caller only passes active rules
    pr_file.refresh_from_db()
    assert pr_file.matched_sensitive_rule_id is None


# --- evaluator-level tests (SENSITIVE_PATH_FORBIDDEN / SENSITIVE_PATH_REVIEW) ---


def _matched_file(path, rule):
    pr_file = PRFileFactory.build(path=path)
    pr_file.matched_sensitive_rule = rule
    return pr_file


def test_sensitive_path_forbidden_fires_for_ai_pr():
    rule = SensitivePathRuleFactory(ai_mode=SensitivePathRule.AiMode.FORBIDDEN)
    ctx = make_context(files=[_matched_file("secrets/key.pem", rule)], sensitive_rules=[rule], is_ai=True)
    findings = list(RULES["SENSITIVE_PATH_FORBIDDEN"](ctx))
    assert len(findings) == 1
    assert findings[0].details_params["sensitive_rule_id"] == rule.pk
    assert findings[0].details_params["path_count"] == 1


def test_sensitive_path_forbidden_does_not_fire_for_non_ai_pr():
    rule = SensitivePathRuleFactory(ai_mode=SensitivePathRule.AiMode.FORBIDDEN)
    ctx = make_context(files=[_matched_file("secrets/key.pem", rule)], sensitive_rules=[rule], is_ai=False)
    assert list(RULES["SENSITIVE_PATH_FORBIDDEN"](ctx)) == []


def test_two_matched_forbidden_rules_produce_two_rows_with_different_hashes():
    from apps.policy.services import details_hash

    rule_a = SensitivePathRuleFactory(ai_mode=SensitivePathRule.AiMode.FORBIDDEN)
    rule_b = SensitivePathRuleFactory(ai_mode=SensitivePathRule.AiMode.FORBIDDEN)
    ctx = make_context(
        files=[_matched_file("a/secret.pem", rule_a), _matched_file("b/secret.pem", rule_b)],
        sensitive_rules=[rule_a, rule_b],
        is_ai=True,
    )
    findings = list(RULES["SENSITIVE_PATH_FORBIDDEN"](ctx))
    assert len(findings) == 2
    hashes = {details_hash(f.identity_params) for f in findings}
    assert len(hashes) == 2


def test_forbidden_paths_capped_with_real_total_in_path_count():
    rule = SensitivePathRuleFactory(ai_mode=SensitivePathRule.AiMode.FORBIDDEN)
    files = [_matched_file(f"secrets/{i}.pem", rule) for i in range(5)]
    ctx = make_context(files=files, sensitive_rules=[rule], is_ai=True, paths_in_params=2)
    findings = list(RULES["SENSITIVE_PATH_FORBIDDEN"](ctx))
    assert len(findings[0].details_params["paths"]) == 2
    assert findings[0].details_params["path_count"] == 5


def test_sensitive_path_review_fires_with_fewer_than_required_approvals():
    from apps.policy.factories import AIPolicyFactory

    rule = SensitivePathRuleFactory(ai_mode=SensitivePathRule.AiMode.NEEDS_EXTRA_REVIEW)
    policy = AIPolicyFactory(min_human_approvals=1)
    ctx = make_context(
        files=[_matched_file("infra/main.tf", rule)],
        sensitive_rules=[rule],
        is_ai=True,
        human_approver_person_ids={1},
        policy=policy,
    )
    findings = list(RULES["SENSITIVE_PATH_REVIEW"](ctx))
    assert len(findings) == 1


def test_sensitive_path_review_does_not_fire_with_enough_approvals():
    from apps.policy.factories import AIPolicyFactory

    rule = SensitivePathRuleFactory(ai_mode=SensitivePathRule.AiMode.NEEDS_EXTRA_REVIEW)
    policy = AIPolicyFactory(min_human_approvals=1)
    ctx = make_context(
        files=[_matched_file("infra/main.tf", rule)],
        sensitive_rules=[rule],
        is_ai=True,
        human_approver_person_ids={1, 2},
        policy=policy,
    )
    findings = list(RULES["SENSITIVE_PATH_REVIEW"](ctx))
    assert findings == []
