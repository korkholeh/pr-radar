"""respx helpers for the REST repository listing discovery and verify_connection() run.

Discovery asks three endpoints (apps/github_sync/discovery.py): `/user/repos`, `/user/orgs` and
`/orgs/{login}/repos`. conftest.py fails the suite on any unmocked request, so a test that reaches
discovery has to answer all three — these helpers keep that in one place rather than in every test.
"""

import httpx
import respx
from django.conf import settings

REPOS_URL = f"{settings.GITHUB_API_BASE_URL}/user/repos"
ORGS_URL = f"{settings.GITHUB_API_BASE_URL}/user/orgs"


def repo_payload(
    node_id: str,
    name: str,
    *,
    owner: str = "acme",
    private: bool = False,
    archived: bool = False,
) -> dict:
    """A REST repository object, shaped like GitHub's, as discovery reads it."""
    return {
        "node_id": node_id,
        "name": name,
        "full_name": f"{owner}/{name}",
        "private": private,
        "archived": archived,
        "default_branch": "main",
        "owner": {"login": owner, "node_id": f"O_{owner}", "type": "Organization"},
    }


def _paged_responses(pages: list[list[dict]]) -> list[httpx.Response]:
    responses = []
    for index, page in enumerate(pages):
        headers = {}
        if index + 1 < len(pages):
            # Only the `next` link matters; the loop follows it verbatim, so the page number in it
            # is what makes each response distinct rather than anything respx matches on.
            headers["Link"] = f'<{REPOS_URL}?page={index + 2}>; rel="next"'
        responses.append(httpx.Response(200, json=page, headers=headers))
    return responses


def mock_repository_listing(
    *pages: list[dict],
    orgs: list[str] | None = None,
    org_repos: list[dict] | None = None,
) -> respx.Route:
    """Answers the whole listing: `/user/repos` with one response per page given, `/user/orgs` with
    `orgs` (empty by default, which skips the organization sweep) and every `/orgs/{login}/repos`
    with `org_repos`. Returns the `/user/repos` route, which is the one tests assert on.

    A single page is answered with the same response however often it is asked for, so a test that
    hits discovery twice (submit, then re-render) needs no second mock. Several pages are chained
    through the `Link` header instead, and are consumed by one listing run."""
    single = len(pages) <= 1
    responses = _paged_responses(list(pages) or [[]])
    route = respx.get(url__startswith=REPOS_URL).mock(
        return_value=responses[0] if single else None,
        side_effect=None if single else responses,
    )
    respx.get(url__startswith=ORGS_URL).mock(
        return_value=httpx.Response(
            200, json=[{"login": login, "node_id": f"O_{login}"} for login in orgs or []]
        )
    )
    respx.get(url__regex=rf"{settings.GITHUB_API_BASE_URL}/orgs/[^/]+/repos.*").mock(
        return_value=httpx.Response(200, json=org_repos or [])
    )
    return route


def mock_repository_listing_error(response: httpx.Response) -> respx.Route:
    """One failing `/user/repos`, for the error paths. The other two endpoints are answered empty so
    a test that gets past the failure does not trip the no-live-HTTP guard instead."""
    route = respx.get(url__startswith=REPOS_URL).mock(return_value=response)
    respx.get(url__startswith=ORGS_URL).mock(return_value=httpx.Response(200, json=[]))
    respx.get(url__regex=rf"{settings.GITHUB_API_BASE_URL}/orgs/[^/]+/repos.*").mock(
        return_value=httpx.Response(200, json=[])
    )
    return route


def fixture_pages(github_fixture, *names: str) -> list[list[dict]]:
    """The named REST list fixtures, as pages for mock_repository_listing()."""
    return [github_fixture(name) for name in names]


__all__ = [
    "ORGS_URL",
    "REPOS_URL",
    "fixture_pages",
    "mock_repository_listing",
    "mock_repository_listing_error",
    "repo_payload",
]
