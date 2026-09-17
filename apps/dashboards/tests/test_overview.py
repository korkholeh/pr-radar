import pytest
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_logged_in_lead_gets_200_and_page_title(client, lead_user):
    client.force_login(lead_user)
    response = client.get(reverse("dashboards:overview"))
    assert response.status_code == 200
    assert "Overview" in response.content.decode()


def test_anonymous_user_is_redirected():
    response = Client().get(reverse("dashboards:overview"))
    assert response.status_code == 302
