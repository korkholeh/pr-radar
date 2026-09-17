import pytest
from django.urls import reverse

from apps.github_sync.models import SyncRun
from apps.github_sync.tests.conftest import mock_graphql_sequence


@pytest.mark.django_db
def test_sync_page_full_page_for_normal_request(client, lead_user):
    client.force_login(lead_user)
    response = client.get(reverse("github_sync:sync"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "<html" in content.lower()
    assert "Sync" in content


@pytest.mark.django_db
def test_sync_page_fragment_for_hx_request(client, lead_user):
    client.force_login(lead_user)
    response = client.get(reverse("github_sync:sync"), HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    content = response.content.decode()
    assert "<html" not in content.lower()
    assert 'id="sync-page"' in content


@pytest.mark.django_db
def test_run_now_enqueues_and_returns_a_fragment(client, admin_user):
    client.force_login(admin_user)
    mock_graphql_sequence()

    response = client.post(reverse("github_sync:run"), HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    assert "<html" not in response.content.decode().lower()
    assert SyncRun.objects.count() == 1


@pytest.mark.django_db
def test_run_now_redirects_for_a_plain_form_post(client, admin_user):
    client.force_login(admin_user)
    mock_graphql_sequence()

    response = client.post(reverse("github_sync:run"))

    assert response.status_code == 302
    assert response.url == reverse("github_sync:sync")


@pytest.mark.django_db
def test_lead_gets_403_triggering_a_sync_but_can_still_view_the_page(client, lead_user):
    client.force_login(lead_user)

    response = client.post(reverse("github_sync:run"))

    assert response.status_code == 403
    assert SyncRun.objects.count() == 0
    assert client.get(reverse("github_sync:sync")).status_code == 200
    assert client.get(reverse("github_sync:status")).status_code == 200


@pytest.mark.django_db
def test_lead_does_not_see_the_sync_now_button(client, lead_user):
    client.force_login(lead_user)

    response = client.get(reverse("github_sync:sync"))

    assert "Sync now" not in response.content.decode()


@pytest.mark.django_db
def test_status_fragment_polls_while_running_and_stops_once_finished(client, lead_user):
    client.force_login(lead_user)
    run = SyncRun.objects.create(trigger=SyncRun.Trigger.UI, status=SyncRun.Status.RUNNING)
    run.stats = {"repositories": 2, "pull_requests": 5, "errors": 0}
    run.save(update_fields=["stats"])

    response = client.get(reverse("github_sync:status"))
    content = response.content.decode()
    assert "hx-trigger" in content
    assert "2" in content and "5" in content

    run.status = SyncRun.Status.SUCCESS
    run.save(update_fields=["status"])

    response = client.get(reverse("github_sync:status"))
    assert "hx-trigger" not in response.content.decode()


@pytest.mark.django_db
def test_run_list_shows_per_connection_stats_and_masked_error_log(client, lead_user):
    client.force_login(lead_user)
    SyncRun.objects.create(
        trigger=SyncRun.Trigger.CLI,
        status=SyncRun.Status.PARTIAL,
        stats={"repositories": 1, "pull_requests": 1, "errors": 1},
        stats_by_connection={
            "1": {"name": "Default", "repositories": 1, "skipped": 0, "errors": 1, "status": "ok"}
        },
        error_log="acme/widget#1: authentication failed [REDACTED]",
    )

    response = client.get(reverse("github_sync:sync"))
    content = response.content.decode()
    assert "Default" in content
    assert "[REDACTED]" in content
    assert "ghp_" not in content


@pytest.mark.django_db
def test_sync_run_list_query_count(client, lead_user, django_assert_max_num_queries):
    client.force_login(lead_user)
    for _ in range(5):
        SyncRun.objects.create(trigger=SyncRun.Trigger.CLI, status=SyncRun.Status.SUCCESS)

    # One query for the run list itself; a handful more for session/auth middleware. Fixed
    # low ceiling so a future N+1 (e.g. per-run related lookups) still fails the build.
    with django_assert_max_num_queries(6):
        client.get(reverse("github_sync:sync"))


CANARY_ENGLISH_STRINGS = [
    ">Sync<",
    "Sync now",
    "Past sync runs",
    "No sync has run yet.",
    "No sync is currently running.",
    "Started",
    "Trigger",
    "Repositories",
    "Errors",
    "By connection",
]


@pytest.mark.django_db
def test_uk_render_has_no_canary_english(client, lead_user):
    client.force_login(lead_user)
    client.post(reverse("accounts:set_language"), {"language": "uk", "next": "/"})
    SyncRun.objects.create(trigger=SyncRun.Trigger.CLI, status=SyncRun.Status.SUCCESS)

    response = client.get(reverse("github_sync:sync"))
    content = response.content.decode()
    for canary in CANARY_ENGLISH_STRINGS:
        assert canary not in content, f"untranslated English string {canary!r} leaked into uk render"
