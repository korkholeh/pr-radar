"""T8: Day mode (plan §2/§3) — `mode=day&day=<date>` renders the day KPI rows and the raw PR
lists/per-person activity table, with a PR merged at 23:59 Kyiv landing on that Kyiv day and not
the UTC one."""

from __future__ import annotations

import datetime
import zoneinfo

import pytest
from django.urls import reverse

from apps.activity.factories import PullRequestFactory, ReviewFactory
from apps.catalog.factories import IdentityFactory, PersonFactory


@pytest.mark.django_db
def test_day_mode_renders_the_day_rows(client, lead_user):
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:overview"), {"mode": "day", "day": "2026-08-15"})
    assert response.status_code == 200
    assert b'data-testid="day-kpis"' in response.content


@pytest.mark.django_db
def test_empty_day_shows_the_explained_empty_state(client, lead_user):
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:overview"), {"mode": "day", "day": "2020-01-01"})
    assert response.status_code == 200
    content = response.content.decode()
    assert "No pull requests opened this day." in content
    assert "No pull requests merged this day." in content
    assert "No activity this day." in content


@pytest.mark.django_db
def test_pr_merged_at_2359_kyiv_lands_on_that_kyiv_day_not_the_utc_one(client, lead_user):
    kyiv = zoneinfo.ZoneInfo("Europe/Kyiv")
    merged_at_kyiv = datetime.datetime(2026, 8, 15, 23, 59, tzinfo=kyiv)
    person = PersonFactory()
    identity = IdentityFactory(person=person)
    pull_request = PullRequestFactory(
        author=identity, state="merged", created_at=merged_at_kyiv, merged_at=merged_at_kyiv
    )

    client.force_login(lead_user)

    kyiv_day_response = client.get(reverse("dashboards:overview"), {"mode": "day", "day": "2026-08-15"})
    utc_day_response = client.get(reverse("dashboards:overview"), {"mode": "day", "day": "2026-08-16"})

    assert f"#{pull_request.number}".encode() in kyiv_day_response.content
    assert f"#{pull_request.number}".encode() not in utc_day_response.content


@pytest.mark.django_db
def test_a_four_digit_pr_number_is_rendered_without_a_thousands_separator(client, lead_user):
    """`USE_THOUSAND_SEPARATOR` is on, and a pull request number is an identifier, not a
    quantity: rendered through it, PR 1208 reads "#1,208" and the GitHub link beside it points at
    `/pull/1,208`. The number here is set explicitly rather than left to the factory sequence --
    the bug only appears past a thousand, which is why it showed up as an order-dependent failure
    in the test above rather than on its own."""
    kyiv = zoneinfo.ZoneInfo("Europe/Kyiv")
    merged_at = datetime.datetime(2026, 8, 15, 12, 0, tzinfo=kyiv)
    identity = IdentityFactory(person=PersonFactory())
    pull_request = PullRequestFactory(
        author=identity, number=1208, state="merged", created_at=merged_at, merged_at=merged_at
    )

    client.force_login(lead_user)
    response = client.get(reverse("dashboards:overview"), {"mode": "day", "day": "2026-08-15"})

    # Every expectation is built from `pull_request.number` rather than written out: a quoted
    # hash followed by four of these digits is also a hex colour, and writing one here would trip
    # the guard in `tests/test_no_hardcoded_colors.py`.
    number = pull_request.number
    separated = f"{number:,}"
    content = response.content.decode()
    assert f"#{number}" in content
    assert separated not in content
    assert f"{number // 1000}\u202f{number % 1000}" not in content

    detail = client.get(reverse("dashboards:pull_request_detail", args=[pull_request.pk]))
    detail_content = detail.content.decode()
    assert f"/pull/{number}" in detail_content
    assert f"/pull/{separated}" not in detail_content


@pytest.mark.django_db
def test_person_activity_table_defaults_to_name_order(client, lead_user):
    kyiv = zoneinfo.ZoneInfo("Europe/Kyiv")
    day_instant = datetime.datetime(2026, 8, 15, 12, 0, tzinfo=kyiv)
    zed = PersonFactory(display_name="Zed")
    ann = PersonFactory(display_name="Ann")
    zed_identity = IdentityFactory(person=zed)
    ann_identity = IdentityFactory(person=ann)
    PullRequestFactory(author=zed_identity, state="open", created_at=day_instant)
    PullRequestFactory(author=ann_identity, state="open", created_at=day_instant)

    client.force_login(lead_user)
    response = client.get(reverse("dashboards:overview"), {"mode": "day", "day": "2026-08-15"})
    content = response.content.decode()
    activity_section = content[content.index('data-testid="day-person-activity"') :]
    assert activity_section.index("Ann") < activity_section.index("Zed")


@pytest.mark.django_db
def test_reviews_given_counted_for_the_reviewer(client, lead_user):
    kyiv = zoneinfo.ZoneInfo("Europe/Kyiv")
    day_instant = datetime.datetime(2026, 8, 15, 12, 0, tzinfo=kyiv)
    reviewer = PersonFactory(display_name="Reviewer Person")
    reviewer_identity = IdentityFactory(person=reviewer)
    pull_request = PullRequestFactory(created_at=day_instant)
    ReviewFactory(pull_request=pull_request, reviewer=reviewer_identity, submitted_at=day_instant)

    client.force_login(lead_user)
    response = client.get(reverse("dashboards:overview"), {"mode": "day", "day": "2026-08-15"})
    assert b"Reviewer Person" in response.content
