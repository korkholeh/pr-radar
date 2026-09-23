import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PRFileFactory, PullRequestFactory
from apps.activity.models import AIDisclosure, AIStatus
from apps.activity.selectors import pull_requests_in_scope
from apps.ai_detection.factories import AISignalFactory, DetectionRuleFactory
from apps.ai_detection.models import Confidence, Detector, Tool
from apps.catalog.factories import ProjectFactory, RepositoryFactory
from apps.churn.factories import ChurnResultFactory
from apps.churn.models import ChurnResult
from apps.policy.factories import PolicyViolationFactory
from apps.policy.models import PolicyViolation


@pytest.mark.django_db
def test_pr_with_no_signals_renders_empty_state(client, lead_user):
    pr = PullRequestFactory()
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:pull_request_detail", args=[pr.pk]))

    assert response.status_code == 200
    assert b"No AI signals detected." in response.content


@pytest.mark.django_db
def test_signal_evidence_is_shown_on_the_pr_page(client, lead_user):
    rule = DetectionRuleFactory(
        detector=Detector.PR_BODY_FOOTER, tool=Tool.CLAUDE_CODE, confidence=Confidence.HIGH
    )
    pr = PullRequestFactory(
        ai_status=AIStatus.AI_EXPLICIT, ai_disclosure=AIDisclosure.SUBSTANTIAL, ai_tools=["claude_code"]
    )
    signal = AISignalFactory(pull_request=pr, rule=rule, evidence="x" * 200)
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:pull_request_detail", args=[pr.pk]))
    content = response.content.decode()

    assert response.status_code == 200
    assert len(signal.evidence) <= 200
    assert signal.evidence in content


@pytest.mark.django_db
def test_resolved_status_disclosure_and_tools_are_shown(client, lead_user):
    pr = PullRequestFactory(
        ai_status=AIStatus.AI_DISCLOSED, ai_disclosure=AIDisclosure.PARTIAL, ai_tools=["copilot"]
    )
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:pull_request_detail", args=[pr.pk]))
    content = response.content.decode()

    assert pr.get_ai_status_display() in content
    assert pr.get_ai_disclosure_display() in content
    assert str(Tool.COPILOT.label) in content
    assert "copilot" not in content


@pytest.mark.django_db
def test_an_unrecognised_tool_falls_back_to_its_raw_text(client, lead_user):
    pr = PullRequestFactory(ai_tools=["somenewtool"])
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:pull_request_detail", args=[pr.pk]))

    assert "somenewtool" in response.content.decode()


@pytest.mark.django_db
def test_signal_list_query_count(client, lead_user):
    rule = DetectionRuleFactory()
    pr = PullRequestFactory()
    for i in range(5):
        AISignalFactory(pull_request=pr, rule=rule, evidence=f"evidence-{i}")
    client.force_login(lead_user)

    with CaptureQueriesContext(connection) as ctx:
        response = client.get(reverse("dashboards:pull_request_detail", args=[pr.pk]))
    assert response.status_code == 200
    assert len(ctx.captured_queries) < 20


@pytest.mark.django_db
def test_unknown_pk_is_404(client, lead_user):
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:pull_request_detail", args=[999999]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_timeline_and_metrics_blocks_render(client, lead_user):
    pr = PullRequestFactory()
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:pull_request_detail", args=[pr.pk]))
    content = response.content.decode()

    assert response.status_code == 200
    assert 'id="pr-timeline"' in content
    assert 'id="pr-metrics"' in content


@pytest.mark.django_db
def test_files_block_shows_badges_and_display_cap(client, lead_user):
    from apps.catalog.services import set_setting

    set_setting("PR_FILES_DISPLAY_LIMIT", 2)
    pr = PullRequestFactory()
    PRFileFactory(pull_request=pr, path="tests/test_a.py", is_test=True)
    PRFileFactory(pull_request=pr, path="app/excluded.py", is_excluded=True)
    PRFileFactory(pull_request=pr, path="app/plain.py")
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:pull_request_detail", args=[pr.pk]))
    content = response.content.decode()

    assert response.status_code == 200
    assert "+1 more file" in content


@pytest.mark.django_db
def test_churn_slot_shows_not_computed_yet_when_missing(client, lead_user):
    pr = PullRequestFactory()
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:pull_request_detail", args=[pr.pk]))

    assert b"Churn not computed yet." in response.content


@pytest.mark.django_db
def test_churn_slot_shows_the_computed_ratio(client, lead_user):
    pr = PullRequestFactory()
    ChurnResultFactory(pull_request=pr, window_days=21, churn_ratio=0.25, lines_at_merge=8, lines_surviving=6)
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:pull_request_detail", args=[pr.pk]))

    assert b"25.0%" in response.content


@pytest.mark.django_db
def test_churn_slot_shows_an_error_row_even_though_status_is_not_ok(client, lead_user):
    """`_pull_request_detail_context` must fetch the latest result **regardless of status**, not
    only `status=ok` rows -- an `error` retry must still render (RISKS row 11)."""
    pr = PullRequestFactory()
    ChurnResultFactory(
        pull_request=pr, window_days=21, status=ChurnResult.Status.ERROR, error="timeout: git timed out"
    )
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:pull_request_detail", args=[pr.pk]))

    assert b"Churn could not be measured." in response.content


@pytest.mark.django_db
def test_churn_slot_shows_unsupported_merge_method(client, lead_user):
    pr = PullRequestFactory()
    ChurnResultFactory(pull_request=pr, window_days=21, status=ChurnResult.Status.UNSUPPORTED_MERGE_METHOD)
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:pull_request_detail", args=[pr.pk]))

    assert b"Churn is not measured for rebase merges." in response.content


@pytest.mark.django_db
def test_churn_slot_shows_too_large(client, lead_user):
    from apps.catalog.services import set_setting

    set_setting("CHURN_MAX_FILES", 50)
    pr = PullRequestFactory()
    ChurnResultFactory(pull_request=pr, window_days=21, status=ChurnResult.Status.TOO_LARGE)
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:pull_request_detail", args=[pr.pk]))

    assert b"more than 50 files" in response.content


@pytest.mark.django_db
def test_churn_slot_shows_error_reason_and_masked_detail(client, lead_user):
    pr = PullRequestFactory()
    ChurnResultFactory(
        pull_request=pr,
        window_days=21,
        status=ChurnResult.Status.ERROR,
        error="no_snapshot: no commit on default branch before window end",
    )
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:pull_request_detail", args=[pr.pk]))
    content = response.content.decode()

    assert "Churn could not be measured." in content
    assert "No commit was found on the default branch" in content
    assert "no commit on default branch before window end" in content


@pytest.mark.django_db
def test_violation_action_swaps_only_the_violations_fragment(client, lead_user):
    pr = PullRequestFactory()
    violation = PolicyViolationFactory(pull_request=pr, status=PolicyViolation.Status.OPEN)
    client.force_login(lead_user)

    response = client.post(
        reverse("dashboards:pull_request_violation_action", args=[pr.pk]),
        {"violation_ids": [violation.pk], "action": "acknowledge", "comment": "reviewed"},
        HTTP_HX_REQUEST="true",
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert 'id="pr-violations"' in content
    assert 'id="pr-timeline"' not in content
    violation.refresh_from_db()
    assert violation.status == PolicyViolation.Status.ACKNOWLEDGED


@pytest.mark.django_db
def test_resolved_violations_are_listed_apart_without_a_checkbox(client, lead_user):
    pr = PullRequestFactory()
    open_violation = PolicyViolationFactory(pull_request=pr, status=PolicyViolation.Status.OPEN)
    resolved = PolicyViolationFactory(pull_request=pr, status=PolicyViolation.Status.RESOLVED)
    client.force_login(lead_user)

    response = client.get(reverse("dashboards:pull_request_detail", args=[pr.pk]))
    content = response.content.decode()

    assert [v.pk for v in response.context["violations"]] == [open_violation.pk]
    assert [v.pk for v in response.context["resolved_violations"]] == [resolved.pk]
    assert f'value="{open_violation.pk}"' in content
    assert f'value="{resolved.pk}"' not in content
    assert 'id="pr-resolved-violations"' in content


@pytest.mark.django_db
def test_only_resolved_violations_show_the_empty_state_and_no_action_bar(client, lead_user):
    pr = PullRequestFactory()
    PolicyViolationFactory(pull_request=pr, status=PolicyViolation.Status.RESOLVED)
    client.force_login(lead_user)

    content = client.get(reverse("dashboards:pull_request_detail", args=[pr.pk])).content.decode()

    assert "No policy violations on this pull request." in content
    assert 'id="pr-violation-action-bar"' not in content


@pytest.mark.django_db
def test_violation_action_full_page_fallback_re_renders_the_whole_page(client, lead_user):
    pr = PullRequestFactory()
    violation = PolicyViolationFactory(pull_request=pr, status=PolicyViolation.Status.OPEN)
    client.force_login(lead_user)

    response = client.post(
        reverse("dashboards:pull_request_violation_action", args=[pr.pk]),
        {"violation_ids": [violation.pk], "action": "waive", "comment": "waived"},
    )

    assert response.status_code == 200
    assert b'id="pr-timeline"' in response.content


@pytest.mark.django_db
def test_violation_action_rejects_a_violation_from_another_pr(client, lead_user):
    pr = PullRequestFactory()
    other_pr = PullRequestFactory()
    other_violation = PolicyViolationFactory(pull_request=other_pr, status=PolicyViolation.Status.OPEN)
    client.force_login(lead_user)

    response = client.post(
        reverse("dashboards:pull_request_violation_action", args=[pr.pk]),
        {"violation_ids": [other_violation.pk], "action": "acknowledge", "comment": "reviewed"},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    other_violation.refresh_from_db()
    assert other_violation.status == PolicyViolation.Status.OPEN


@pytest.mark.django_db
def test_restricted_scope_selector_composition_hides_other_projects_pr():
    project = ProjectFactory()
    own_repository = RepositoryFactory()
    project.repositories.add(own_repository)
    other_repository = RepositoryFactory()

    own_pr = PullRequestFactory(repository=own_repository)
    other_pr = PullRequestFactory(repository=other_repository)

    scope = ScopeFilter(unrestricted=False, project_ids=frozenset({project.pk}))
    visible_ids = set(pull_requests_in_scope(scope).values_list("pk", flat=True))

    assert own_pr.pk in visible_ids
    assert other_pr.pk not in visible_ids
