"""Repository discovery over REST: the mapping, the Link-header pagination and the organization
sweep that exists because GraphQL's viewer connection hides an org-owned fine-grained token's
grants."""

import httpx
import pytest
import respx
from django.conf import settings

from apps.github_sync.discovery import repository_node, repository_nodes
from apps.github_sync.errors import GitHubAuthError, GitHubError, GitHubSchemaError
from apps.github_sync.tests.test_client import make_client

REPOS_URL = f"{settings.GITHUB_API_BASE_URL}/user/repos"
ORGS_URL = f"{settings.GITHUB_API_BASE_URL}/user/orgs"


def rest_repo(node_id, name, owner="acme", *, private=False, archived=False, default_branch="main"):
    return {
        "node_id": node_id,
        "name": name,
        "full_name": f"{owner}/{name}",
        "private": private,
        "archived": archived,
        "default_branch": default_branch,
        "owner": {"login": owner, "node_id": f"O_{owner}", "type": "Organization"},
    }


def mock_orgs(*logins):
    return respx.get(url__startswith=ORGS_URL).mock(
        return_value=httpx.Response(200, json=[{"login": login} for login in logins])
    )


def mock_org_repos(login, response):
    return respx.get(url__startswith=f"{settings.GITHUB_API_BASE_URL}/orgs/{login}/repos").mock(
        return_value=response
    )


def test_repository_node_maps_rest_fields_onto_the_node_shape_downstream_expects():
    node = repository_node(rest_repo("R_1", "widget", private=True))

    assert node == {
        "id": "R_1",
        "name": "widget",
        "nameWithOwner": "acme/widget",
        "isPrivate": True,
        "isArchived": False,
        "defaultBranchRef": {"name": "main"},
        "owner": {"id": "O_acme", "login": "acme", "__typename": "Organization"},
    }


def test_repository_node_keeps_node_id_so_existing_rows_still_match():
    """Repository.github_id was written from GraphQL ids before discovery moved to REST. REST's
    node_id is that same global id, which is why the move needs no migration."""
    assert repository_node(rest_repo("R_kgDOA1", "widget"))["id"] == "R_kgDOA1"


def test_repository_node_reports_an_empty_repository_as_having_no_default_branch():
    node = repository_node(rest_repo("R_1", "fresh", default_branch=""))
    assert node["defaultBranchRef"] is None


def test_repository_node_refuses_a_payload_without_a_node_id():
    payload = {"name": "widget", "full_name": "acme/widget", "private": False, "owner": {}}
    with pytest.raises(GitHubSchemaError) as exc_info:
        repository_node(payload)
    assert exc_info.value.path == "node_id"


@pytest.mark.django_db
def test_listing_follows_the_link_header_across_pages():
    respx.get(url__startswith=REPOS_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json=[rest_repo("R_1", "widget")],
                headers={"Link": f'<{REPOS_URL}?page=2>; rel="next"'},
            ),
            httpx.Response(200, json=[rest_repo("R_2", "gadget")]),
        ]
    )
    mock_orgs()

    nodes = repository_nodes(make_client(), page_size=100)

    assert [node["nameWithOwner"] for node in nodes] == ["acme/widget", "acme/gadget"]


@pytest.mark.django_db
def test_listing_asks_for_the_requested_page_size_and_every_affiliation():
    route = respx.get(url__startswith=REPOS_URL).mock(return_value=httpx.Response(200, json=[]))
    mock_orgs()

    repository_nodes(make_client(), page_size=100)

    request_url = str(route.calls.last.request.url)
    assert "per_page=100" in request_url
    assert "affiliation=owner%2Ccollaborator%2Corganization_member" in request_url


@pytest.mark.django_db
def test_listing_refuses_a_link_header_pointing_at_another_host():
    """The next page is an absolute URL out of a response header; following it blindly would hand
    the token to whatever host it names."""
    respx.get(url__startswith=REPOS_URL).mock(
        return_value=httpx.Response(
            200,
            json=[rest_repo("R_1", "widget")],
            headers={"Link": '<https://evil.example.com/user/repos?page=2>; rel="next"'},
        )
    )
    mock_orgs()

    with pytest.raises(GitHubError):
        repository_nodes(make_client(), page_size=100)


@pytest.mark.django_db
def test_organization_repositories_are_listed_even_when_the_account_listing_misses_them():
    """The bug this module exists for: an organization-owned fine-grained token reads the org's
    repositories but appears affiliated with none of them, so `/user/repos` comes back empty."""
    respx.get(url__startswith=REPOS_URL).mock(return_value=httpx.Response(200, json=[]))
    mock_orgs("acme")
    mock_org_repos("acme", httpx.Response(200, json=[rest_repo("R_5", "internal-tools", private=True)]))

    nodes = repository_nodes(make_client(), page_size=100)

    assert [node["nameWithOwner"] for node in nodes] == ["acme/internal-tools"]


@pytest.mark.django_db
def test_a_repository_reachable_both_ways_is_listed_once():
    respx.get(url__startswith=REPOS_URL).mock(
        return_value=httpx.Response(200, json=[rest_repo("R_2", "gadget")])
    )
    mock_orgs("acme")
    mock_org_repos("acme", httpx.Response(200, json=[rest_repo("R_2", "gadget")]))

    nodes = repository_nodes(make_client(), page_size=100)

    assert [node["id"] for node in nodes] == ["R_2"]


@pytest.mark.django_db
def test_an_owner_login_passed_in_is_swept_even_when_memberships_cannot_be_listed():
    """A fine-grained token without organization-read permission cannot list its memberships. The
    connection's own owner_login is the one organization a lead is sure to care about, so it is
    swept anyway."""
    respx.get(url__startswith=REPOS_URL).mock(return_value=httpx.Response(200, json=[]))
    respx.get(url__startswith=ORGS_URL).mock(
        return_value=httpx.Response(403, json={"message": "Resource not accessible by personal access token"})
    )
    mock_org_repos("acme", httpx.Response(200, json=[rest_repo("R_5", "internal-tools")]))

    nodes = repository_nodes(make_client(), page_size=100, owner_logins=["acme"])

    assert [node["nameWithOwner"] for node in nodes] == ["acme/internal-tools"]


@pytest.mark.django_db
def test_an_organization_the_token_cannot_read_is_skipped_not_fatal():
    respx.get(url__startswith=REPOS_URL).mock(
        return_value=httpx.Response(200, json=[rest_repo("R_1", "widget")])
    )
    mock_orgs("acme", "locked-org")
    mock_org_repos("acme", httpx.Response(200, json=[]))
    mock_org_repos("locked-org", httpx.Response(404, json={"message": "Not Found"}))

    nodes = repository_nodes(make_client(), page_size=100)

    assert [node["nameWithOwner"] for node in nodes] == ["acme/widget"]


@pytest.mark.django_db
def test_an_empty_owner_login_adds_no_organization_request():
    """A connection with no owner_login must not produce a request to `/orgs//repos`."""
    respx.get(url__startswith=REPOS_URL).mock(return_value=httpx.Response(200, json=[]))
    mock_orgs()
    org_route = respx.get(url__regex=rf"{settings.GITHUB_API_BASE_URL}/orgs/.*").mock(
        return_value=httpx.Response(200, json=[])
    )

    assert repository_nodes(make_client(), page_size=100, owner_logins=[""]) == []
    assert org_route.call_count == 0


@pytest.mark.django_db
def test_a_denied_account_listing_is_survivable_when_an_organization_answers():
    """A token can be refused `/user/repos` and still read an organization's repositories. Raising
    on the account listing would throw away exactly the list this module exists to produce."""
    respx.get(url__startswith=REPOS_URL).mock(
        return_value=httpx.Response(403, json={"message": "Resource not accessible by personal access token"})
    )
    mock_orgs()
    mock_org_repos("acme", httpx.Response(200, json=[rest_repo("R_5", "internal-tools")]))

    nodes = repository_nodes(make_client(), page_size=100, owner_logins=["acme"])

    assert [node["nameWithOwner"] for node in nodes] == ["acme/internal-tools"]


@pytest.mark.django_db
def test_a_denied_account_listing_is_raised_when_nothing_else_is_visible():
    """With no repository listed anywhere, the denial is the answer — verify_connection() turns it
    into REPOS_VISIBLE_NONE rather than reporting a healthy, empty connection."""
    respx.get(url__startswith=REPOS_URL).mock(return_value=httpx.Response(403, json={"message": "nope"}))
    mock_orgs()

    with pytest.raises(GitHubAuthError):
        repository_nodes(make_client(), page_size=100)
