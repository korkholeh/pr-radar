import datetime
import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time

from apps.accounts.selectors import scope_for_user
from apps.activity.factories import PullRequestFactory
from apps.activity.models import AIStatus
from apps.policy.factories import PolicyViolationFactory
from apps.policy.messages import render_violation, rule_label
from apps.policy.models import PolicyViolation
from apps.policy.selectors import violations_by_rule

pytestmark = pytest.mark.django_db

RuleCode = PolicyViolation.RuleCode


def test_anonymous_is_redirected(client):
    response = client.get(reverse("policy:console"))
    assert response.status_code == 302


def test_lead_gets_200(client, lead_user):
    client.force_login(lead_user)
    assert client.get(reverse("policy:console")).status_code == 200


def test_table_shows_rendered_violation_sentence(client, lead_user):
    violation = PolicyViolationFactory(
        rule_code=RuleCode.NO_TESTS, details_params={"non_test_lines": 120, "threshold": 20}
    )
    client.force_login(lead_user)

    response = client.get(reverse("policy:console"))

    expected = render_violation(RuleCode.NO_TESTS, {"non_test_lines": 120, "threshold": 20})
    assert expected.encode() in response.content
    assert str(violation.pull_request.repository.full_name).encode() in response.content


@freeze_time("2026-06-15T12:00:00Z")
def test_chart_table_alternative_matches_violations_by_rule(client, lead_user):
    PolicyViolationFactory(rule_code=RuleCode.NO_TESTS)
    PolicyViolationFactory(rule_code=RuleCode.NO_TESTS)
    PolicyViolationFactory(rule_code=RuleCode.SELF_MERGE)
    client.force_login(lead_user)

    response = client.get(reverse("policy:console"))
    content = response.content.decode()

    scope = scope_for_user(lead_user)
    today = timezone.localdate()
    series = violations_by_rule(scope, today - datetime.timedelta(days=29), today)
    assert series  # sanity: the fixtures above must actually produce rows to compare against

    table_match = re.search(r'<table[^>]*id="policy-rule-chart-table".*?</table>', content, re.DOTALL)
    assert table_match is not None
    rows = re.findall(r"<tr><td>(.*?)</td><td>(\d+)</td></tr>", table_match.group(0))
    assert rows == [(rule_label(row.rule_code), str(row.count)) for row in series]


@freeze_time("2026-06-15T12:00:00Z")
def test_low_sample_kpi_is_marked(client, lead_user):
    for _ in range(3):  # below MIN_SAMPLE (5): a real, greyed compliance rate, not the None branch
        PullRequestFactory(ai_status=AIStatus.AI_EXPLICIT)
    client.force_login(lead_user)

    response = client.get(reverse("policy:console"))

    assert b"data-low-sample" in response.content
    assert b'data-compliance-rate="none"' not in response.content


def test_empty_state_renders_with_no_violations(client, lead_user):
    client.force_login(lead_user)
    response = client.get(reverse("policy:console"))
    assert response.status_code == 200
    assert b'data-testid="empty-state"' in response.content
    assert b"No policy violations match these filters." in response.content


def test_filters_narrow_the_table(client, lead_user):
    PolicyViolationFactory(rule_code=RuleCode.NO_TESTS)
    PolicyViolationFactory(rule_code=RuleCode.SELF_MERGE)
    client.force_login(lead_user)

    response = client.get(reverse("policy:console"), {"rule_code": RuleCode.SELF_MERGE, "status": "open"})

    assert response.context["page_obj"].paginator.count == 1


def test_filters_survive_in_the_htmx_fragment(client, lead_user):
    PolicyViolationFactory(rule_code=RuleCode.NO_TESTS)
    PolicyViolationFactory(rule_code=RuleCode.SELF_MERGE)
    client.force_login(lead_user)

    response = client.get(
        reverse("policy:console"), {"rule_code": RuleCode.SELF_MERGE}, HTTP_HX_REQUEST="true"
    )

    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 1


def test_htmx_request_returns_fragment_normal_request_returns_full_page(client, lead_user):
    client.force_login(lead_user)

    full_page = client.get(reverse("policy:console"))
    fragment = client.get(reverse("policy:console"), HTTP_HX_REQUEST="true")

    assert b"<html" in full_page.content
    assert b"<html" not in fragment.content


def test_console_query_count_is_bounded(client, lead_user):
    for _ in range(10):
        PolicyViolationFactory()
    client.force_login(lead_user)

    with CaptureQueriesContext(connection) as ctx:
        response = client.get(reverse("policy:console"))
    assert response.status_code == 200
    assert len(ctx.captured_queries) < 20
