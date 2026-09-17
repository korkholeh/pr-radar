import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory
from apps.activity.models import AIDisclosure, AIStatus
from apps.activity.selectors import pull_requests_in_scope
from apps.ai_detection.factories import AISignalFactory, DetectionRuleFactory
from apps.ai_detection.models import Confidence, Detector, Tool
from apps.catalog.factories import ProjectFactory, RepositoryFactory


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
    assert len(ctx.captured_queries) < 10


@pytest.mark.django_db
def test_unknown_pk_is_404(client, lead_user):
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:pull_request_detail", args=[999999]))
    assert response.status_code == 404


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
