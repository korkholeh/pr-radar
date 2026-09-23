import datetime
import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone, translation
from freezegun import freeze_time

from apps.accounts.selectors import scope_for_user
from apps.activity.factories import PullRequestFactory
from apps.activity.models import AIStatus, PullRequest
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
    rows = re.findall(
        r"<tr><td><a [^>]*>(.*?)</a></td><td>(\d+)</td><td>(\d+)</td></tr>", table_match.group(0)
    )
    assert rows == [(rule_label(row.rule_code), str(row.count), str(row.open_count)) for row in series]


@freeze_time("2026-06-15T12:00:00Z")
def test_chart_splits_open_from_closed_and_links_to_the_same_rows(client, lead_user):
    """The chart counts every status over the period while the table defaults to open only, so a
    rule whose violations were all auto-resolved shows a bar and an empty table. The bar says how
    many are open, and its link opens the table on exactly the rows it counts."""
    PolicyViolationFactory(rule_code=RuleCode.TOOL_NOT_ALLOWED, status=PolicyViolation.Status.RESOLVED)
    PolicyViolationFactory(rule_code=RuleCode.TOOL_NOT_ALLOWED, status=PolicyViolation.Status.RESOLVED)
    PolicyViolationFactory(rule_code=RuleCode.TOOL_NOT_ALLOWED)
    client.force_login(lead_user)

    content = client.get(reverse("policy:console")).content.decode()
    table = re.search(r'<table[^>]*id="policy-rule-chart-table".*?</table>', content, re.DOTALL).group(0)
    link, count, open_count = re.search(
        r'<tr><td><a class="link" href="([^"]+)">[^<]*</a></td><td>(\d+)</td><td>(\d+)</td></tr>', table
    ).groups()
    assert (count, open_count) == ("3", "1")

    followed = client.get(link.replace("&amp;", "&").split("#")[0])
    followed_ids = {violation.pk for violation in followed.context["page_obj"].object_list}
    assert followed_ids == set(PolicyViolation.objects.values_list("pk", flat=True))


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


@freeze_time("2026-06-15T12:00:00Z")
def test_table_shows_and_orders_by_the_pull_requests_date_not_the_recording_time(client, lead_user):
    """A sync writes a whole backfill's violations in one run, so their own `created_at` is the
    same for hundreds of rows; the pull request's opening date is what tells them apart."""
    older_pr = PullRequestFactory(created_at=datetime.datetime(2026, 5, 2, 9, tzinfo=datetime.UTC))
    newer_pr = PullRequestFactory(created_at=datetime.datetime(2026, 6, 10, 9, tzinfo=datetime.UTC))
    older = PolicyViolationFactory(pull_request=older_pr)
    newer = PolicyViolationFactory(pull_request=newer_pr)
    client.force_login(lead_user)

    response = client.get(reverse("policy:console"))

    assert [violation.pk for violation in response.context["page_obj"]] == [newer.pk, older.pk]
    content = response.content.decode()
    row = re.search(rf'<tr id="violation-row-{older.pk}">.*?</tr>', content, re.DOTALL).group(0)
    assert "05/02/2026" in row or "02.05.2026" in row


def test_table_shows_the_pull_requests_state_apart_from_the_violations_status(client, lead_user):
    """A violation's "open" is the lead's triage state; on a merged pull request it used to read,
    in Ukrainian, exactly like the pull request's own "open" state."""
    merged_pr = PullRequestFactory(state=PullRequest.State.MERGED, merged_at=timezone.now())
    violation = PolicyViolationFactory(pull_request=merged_pr)
    client.force_login(lead_user)

    with translation.override("uk"):
        content = client.get(reverse("policy:console")).content.decode()
        merged_label = str(PullRequest.State.MERGED.label)
        pr_open_label = str(PullRequest.State.OPEN.label)
        violation_open_label = str(PolicyViolation.Status.OPEN.label)

    row = re.search(rf'<tr id="violation-row-{violation.pk}">.*?</tr>', content, re.DOTALL).group(0)
    assert merged_label in row
    assert violation_open_label in row
    assert violation_open_label != pr_open_label


@freeze_time("2026-06-15T12:00:00Z")
def test_date_filter_uses_the_pull_requests_opening_day(client, lead_user):
    may_pr = PullRequestFactory(created_at=datetime.datetime(2026, 5, 2, 9, tzinfo=datetime.UTC))
    june_pr = PullRequestFactory(created_at=datetime.datetime(2026, 6, 10, 9, tzinfo=datetime.UTC))
    PolicyViolationFactory(pull_request=may_pr)  # recorded today, like its sibling
    june_violation = PolicyViolationFactory(pull_request=june_pr)
    client.force_login(lead_user)

    response = client.get(reverse("policy:console"), {"date_from": "2026-06-01", "date_to": "2026-06-15"})

    assert [violation.pk for violation in response.context["page_obj"]] == [june_violation.pk]
