"""The Sync page's backfill panel: preset windows, an explicit start date, repository narrowing,
and the permission/validation edges around them."""

import datetime

import pytest
from django.urls import reverse
from freezegun import freeze_time

from apps.catalog.factories import RepositoryFactory
from apps.connections.factories import GitHubConnectionFactory
from apps.connections.services import set_token
from apps.github_sync.forms import BackfillForm
from apps.github_sync.models import SyncRun
from apps.github_sync.tests.conftest import mock_graphql_sequence
from apps.github_sync.tests.test_sync import _one_pr_sequence
from apps.metrics.timeframe import day_start

TOKEN = "ghp_secrettokenvalue0123456789"


def _make_repository(**kwargs):
    connection = GitHubConnectionFactory()
    set_token(connection, TOKEN)
    return RepositoryFactory(connection=connection, **kwargs)


@pytest.mark.django_db
@freeze_time("2026-09-19 08:00:00")
def test_preset_window_backfills_from_that_many_days_ago(client, admin_user):
    client.force_login(admin_user)
    _make_repository(full_name="acme/widget")
    mock_graphql_sequence(*_one_pr_sequence())

    response = client.post(reverse("github_sync:backfill"), {"window": "14"}, HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    run = SyncRun.objects.get()
    assert run.trigger == SyncRun.Trigger.BACKFILL
    assert run.since == day_start(datetime.date(2026, 9, 5))


@pytest.mark.django_db
def test_custom_date_is_used_as_the_watermark(client, admin_user):
    client.force_login(admin_user)
    repository = _make_repository(
        full_name="acme/widget",
        last_synced_at=datetime.datetime(2026, 1, 10, tzinfo=datetime.UTC),
    )
    mock_graphql_sequence(*_one_pr_sequence(updated_at="2026-01-05T09:00:00Z"))

    client.post(
        reverse("github_sync:backfill"),
        {"window": "custom", "since": "2026-01-01"},
        HTTP_HX_REQUEST="true",
    )

    # The PR predates last_synced_at, so only an overridden watermark can have pulled it in.
    assert repository.pull_requests.count() == 1
    assert SyncRun.objects.get().since == day_start(datetime.date(2026, 1, 1))


@pytest.mark.django_db
def test_selected_repositories_narrow_the_backfill(client, admin_user):
    client.force_login(admin_user)
    chosen = _make_repository(full_name="acme/widget")
    _make_repository(full_name="acme/other")
    mock_graphql_sequence(*_one_pr_sequence())

    client.post(
        reverse("github_sync:backfill"),
        {"window": "7", "repositories": [chosen.pk]},
        HTTP_HX_REQUEST="true",
    )

    run = SyncRun.objects.get()
    assert run.stats["repositories"] == 1
    assert list(run.repositories.values_list("full_name", flat=True)) == ["acme/widget"]


@pytest.mark.django_db
def test_custom_window_without_a_date_returns_a_visible_error_and_runs_nothing(client, admin_user):
    client.force_login(admin_user)
    _make_repository(full_name="acme/widget")

    response = client.post(reverse("github_sync:backfill"), {"window": "custom"}, HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    content = response.content.decode()
    assert "<html" not in content.lower()
    assert "Pick a start date." in content
    assert SyncRun.objects.count() == 0


@pytest.mark.django_db
@freeze_time("2026-09-19 08:00:00")
def test_a_future_start_date_is_rejected(client, admin_user):
    client.force_login(admin_user)
    _make_repository(full_name="acme/widget")

    response = client.post(
        reverse("github_sync:backfill"),
        {"window": "custom", "since": "2026-09-20"},
        HTTP_HX_REQUEST="true",
    )

    assert "The start date cannot be in the future." in response.content.decode()
    assert SyncRun.objects.count() == 0


@pytest.mark.django_db
def test_plain_post_redirects_back_to_the_sync_page(client, admin_user):
    client.force_login(admin_user)
    _make_repository(full_name="acme/widget")
    mock_graphql_sequence(*_one_pr_sequence())

    response = client.post(reverse("github_sync:backfill"), {"window": "7"})

    assert response.status_code == 302
    assert response.url == reverse("github_sync:sync")


@pytest.mark.django_db
def test_lead_cannot_trigger_or_see_the_backfill_panel(client, lead_user):
    client.force_login(lead_user)

    assert client.post(reverse("github_sync:backfill"), {"window": "7"}).status_code == 403
    assert SyncRun.objects.count() == 0
    assert "Load historical data" not in client.get(reverse("github_sync:sync")).content.decode()


@pytest.mark.django_db
def test_admin_sees_the_panel_with_only_active_repositories(client, admin_user):
    client.force_login(admin_user)
    _make_repository(full_name="acme/widget")
    _make_repository(full_name="acme/archived", is_active=False)

    content = client.get(reverse("github_sync:sync")).content.decode()

    assert "Load historical data" in content
    assert "acme/widget" in content
    assert "acme/archived" not in content


@pytest.mark.django_db
@freeze_time("2026-09-19 22:30:00")
def test_window_is_counted_in_report_timezone_not_utc():
    """22:30 UTC is already the 20th in Kyiv: a "last 7 days" window must start on the 13th."""
    form = BackfillForm({"window": "7"})

    assert form.is_valid()
    assert form.cleaned_data["since"] == datetime.date(2026, 9, 13)


@pytest.mark.django_db
def test_backfill_run_is_listed_with_its_start_date(client, admin_user):
    client.force_login(admin_user)
    SyncRun.objects.create(
        trigger=SyncRun.Trigger.BACKFILL,
        status=SyncRun.Status.SUCCESS,
        since=day_start(datetime.date(2026, 9, 1)),
    )

    content = client.get(reverse("github_sync:sync")).content.decode()

    assert "2026-09-01" in content


CANARY_ENGLISH_STRINGS = [
    "Load historical data",
    "Start backfill",
    "Start date",
    "Last 14 days",
    "From a specific date",
    "Leave empty to backfill every active repository.",
]


@pytest.mark.django_db
def test_uk_render_of_the_backfill_panel_has_no_canary_english(client, admin_user):
    client.force_login(admin_user)
    client.post(reverse("accounts:set_language"), {"language": "uk", "next": "/"})
    _make_repository(full_name="acme/widget")

    content = client.get(reverse("github_sync:sync")).content.decode()

    for canary in CANARY_ENGLISH_STRINGS:
        assert canary not in content, f"untranslated English string {canary!r} leaked into uk render"


@pytest.mark.django_db
def test_uk_render_of_a_validation_error_has_no_canary_english(client, admin_user):
    client.force_login(admin_user)
    client.post(reverse("accounts:set_language"), {"language": "uk", "next": "/"})

    response = client.post(reverse("github_sync:backfill"), {"window": "custom"}, HTTP_HX_REQUEST="true")

    assert "Pick a start date." not in response.content.decode()
    assert "Вкажіть дату початку." in response.content.decode()
