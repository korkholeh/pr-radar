"""respx helpers shared by github_sync tests: bind one or more fixture bodies, in call order,
to the GraphQL or REST endpoints under test."""

import httpx
import respx
from django.conf import settings


def mock_graphql_sequence(*bodies: dict, status_code: int = 200) -> respx.Route:
    """Every GraphQL POST hits the same URL; successive calls receive `bodies` in order."""
    responses = [httpx.Response(status_code, json=body) for body in bodies]
    return respx.post(settings.GITHUB_GRAPHQL_URL).mock(side_effect=responses)


def mock_graphql_responses(*responses: httpx.Response) -> respx.Route:
    """Like mock_graphql_sequence, but callers control status code and headers per response."""
    return respx.post(settings.GITHUB_GRAPHQL_URL).mock(side_effect=list(responses))


def mock_rest_sequence(path: str, *responses: httpx.Response) -> respx.Route:
    return respx.get(f"{settings.GITHUB_API_BASE_URL}{path}").mock(side_effect=list(responses))
