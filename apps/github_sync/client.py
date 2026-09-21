"""httpx-based GraphQL/REST client with pagination, retries and rate budgeting."""

import logging
import re
import time
from collections.abc import Callable, Generator, Iterable
from datetime import datetime

import httpx
from django.conf import settings

from apps.catalog.services import get_int
from apps.github_sync.errors import (
    GitHubAuthError,
    GitHubError,
    GitHubNotFoundError,
    GitHubServerError,
    GitHubSSOError,
    GitHubWriteAttemptError,
    SecondaryRateLimitError,
)
from apps.github_sync.errors import require as require_path
from apps.github_sync.rate_limit import RateBudget, backoff_delays, retry_after_seconds
from config.security import mask_secrets

logger = logging.getLogger(__name__)

_RETRYABLE_STATUSES = (502, 503, 504)
_SSO_HEADER = "X-GitHub-SSO"


def _parse_iso8601(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


_WRITE_KEYWORDS = ("mutation", "subscription")


def assert_read_only(document: str) -> None:
    """Refuses any GraphQL document that is not a plain query. PR Radar reads GitHub and never
    writes to it (ADR 0003, and the read-only PAT in the architecture diagram). REST is read-only
    by construction -- `rest_get()`/`rest_paginate()` are the only REST entry points and both send
    GET -- but GraphQL sends reads and writes alike as a POST, so nothing about the request itself
    carries the promise. This puts it on the document: every operation definition must be a `query`.

    The check is line-based because every document in `queries.py` opens its operations at the
    start of a line. That makes it strict rather than clever: an operation keyword indented into
    a selection set is rejected too, which is the safe direction for a guard whose whole job is
    to fail closed."""
    seen_operation = False
    for line in document.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        head = stripped.split("(")[0].split("{")[0].strip()
        # A line that opens with a brace -- the anonymous `{ viewer { login } }` shorthand --
        # leaves `head` empty; fall back to the raw first token so it is judged, not skipped.
        keyword = head.split()[0] if head else stripped.split()[0]
        if keyword in _WRITE_KEYWORDS:
            raise GitHubWriteAttemptError(keyword)
        if not seen_operation:
            if keyword != "query":
                raise GitHubWriteAttemptError(keyword)
            seen_operation = True


_LINK_NEXT_RE = re.compile(r'<(?P<url>[^>]+)>\s*;\s*rel="next"')


def _next_page_url(link_header: str | None) -> str | None:
    """The `next` URL out of a REST `Link` header, or None on the last page."""
    if not link_header:
        return None
    match = _LINK_NEXT_RE.search(link_header)
    return match.group("url") if match else None


def _is_nested_path(path: object) -> bool:
    """True for a GraphQL error path that reaches below a root field. A path of one segment names
    the document's own top-level field, and anything that is not a list of segments is treated as
    no path at all -- both mean the request as a whole was refused."""
    return isinstance(path, list) and len(path) > 1


def _describe_graphql_error(error: dict) -> str:
    """One GraphQL error as `TYPE at a.b.c: message`, so `SyncRun.error_log` says which field the
    token was refused rather than only that something was. Every part comes from GitHub's own
    error envelope, never from repository content, and the caller masks the line anyway."""
    parts = []
    if error.get("type"):
        parts.append(str(error["type"]))
    path = error.get("path")
    if isinstance(path, list) and path:
        parts.append("at " + ".".join(str(segment) for segment in path))
    prefix = " ".join(parts)
    message = str(error.get("message", "unknown error"))
    return f"{prefix}: {message}" if prefix else message


def _parse_sso_header(value: str) -> tuple[str, str]:
    url = ""
    for part in value.split(";"):
        part = part.strip()
        if part.startswith("url="):
            url = part[len("url=") :]
    match = re.search(r"/orgs/([^/]+)/sso", url)
    org = match.group(1) if match else ""
    return org, url


class GitHubClient:
    def __init__(
        self,
        auth,
        budget: RateBudget,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.auth = auth
        self.budget = budget
        self._client = client or httpx.Client()
        self.sleep = sleep
        self.graphql_url = settings.GITHUB_GRAPHQL_URL
        self.base_url = settings.GITHUB_API_BASE_URL
        self._max_retries = get_int("SYNC_MAX_RETRIES")
        self._max_backoff = get_int("SYNC_RETRY_MAX_SECONDS")
        self._min_remaining = get_int("RATE_LIMIT_MIN_REMAINING")

    def graphql(self, document: str, variables: dict) -> dict:
        assert_read_only(document)
        self._wait_for_budget()
        response = self._request_with_retries(
            "POST", self.graphql_url, json={"query": document, "variables": variables}
        )
        body = response.json()
        errors = body.get("errors")
        if errors:
            self._handle_graphql_errors(errors)
        data = body.get("data") or {}
        rate_limit = data.get("rateLimit")
        if rate_limit:
            self.budget.update(
                remaining=require_path(data, "rateLimit.remaining"),
                reset_at=_parse_iso8601(require_path(data, "rateLimit.resetAt")),
            )
        return data

    def rest_get(self, path: str) -> tuple[dict, httpx.Headers]:
        self._wait_for_budget()
        response = self._request_with_retries("GET", f"{self.base_url}{path}")
        return response.json(), response.headers

    def rest_paginate(self, path: str, *, page_size: int) -> Generator[dict, None, None]:
        """A REST list endpoint, page by page, following the `Link: rel="next"` header. REST has no
        cursor in the body -- the next page is only ever the URL GitHub hands back -- so the loop
        follows that URL rather than building one. It is checked against the configured API base
        first: an absolute URL taken from a response header is untrusted input, and a redirected
        host would otherwise receive the token."""
        url = str(httpx.URL(f"{self.base_url}{path}").copy_set_param("per_page", page_size))
        while True:
            self._wait_for_budget()
            response = self._request_with_retries("GET", url)
            payload = response.json()
            if not isinstance(payload, list):
                raise GitHubError(f"Expected a JSON array from {path}.")
            yield from payload
            next_url = _next_page_url(response.headers.get("Link"))
            if next_url is None:
                break
            if not next_url.startswith(f"{self.base_url}/"):
                raise GitHubError("Pagination Link header pointed outside the configured GitHub API.")
            url = next_url

    def paginate(
        self, document: str, variables: dict, *, page_path: str, page_size: int
    ) -> Generator[dict, None, None]:
        after = variables.get("after")
        base_variables = {k: v for k, v in variables.items() if k not in ("first", "after")}
        while True:
            data = self.graphql(document, {**base_variables, "first": page_size, "after": after})
            # require_path() is called against the full payload with the qualified path (rather
            # than an already-unwrapped sub-dict) so a GitHubSchemaError names which connection
            # and page drifted, e.g. "node.reviews.pageInfo.endCursor", not a bare "endCursor".
            nodes = require_path(data, f"{page_path}.nodes")
            yield from nodes
            has_next = require_path(data, f"{page_path}.pageInfo.hasNextPage")
            if not has_next:
                break
            after = require_path(data, f"{page_path}.pageInfo.endCursor")

    def _wait_for_budget(self) -> None:
        wait_seconds = self.budget.wait_duration(self._min_remaining)
        if wait_seconds > 0:
            logger.info(
                "Rate budget low for %s (remaining=%s); waiting %.1fs until reset.",
                self.budget.key,
                self.budget.remaining,
                wait_seconds,
            )
            self.sleep(wait_seconds)

    def _handle_graphql_errors(self, errors: Iterable[dict]) -> None:
        """Sorts a GraphQL `errors` array into the three things it can mean.

        GraphQL answers 200 for all of them, so the distinction is in the entries. An entry with
        no `type` of ours is a plain failure and raises `GitHubError`. A denial (`FORBIDDEN` /
        `UNAUTHORIZED`) is split by how deep its `path` reaches: a missing path, or one naming a
        root field such as `repository`, means the token may not read the thing the document was
        about, and that is a credential problem worth quarantining the connection for. A deeper
        path is a *field-level* denial -- GitHub resolves the rest of the document, nulls that one
        field and reports it here -- which a fine-grained token gets for every field outside its
        permission set (`statusCheckRollup` without **Checks**, for one). Raising on those turned
        a missing optional permission into "authentication failed" and marked the whole connection
        invalid, so they are logged once and the resolved data is used as it stands; a field the
        sync actually needs still fails downstream, in `require()`, naming its path.
        """
        denials: list[dict] = []
        failures: list[dict] = []
        for error in errors:
            (denials if error.get("type") in ("FORBIDDEN", "UNAUTHORIZED") else failures).append(error)

        if failures:
            raise GitHubError("; ".join(_describe_graphql_error(e) for e in failures))

        fatal = [error for error in denials if not _is_nested_path(error.get("path"))]
        if fatal:
            raise GitHubAuthError(
                "GraphQL request was not authorized: " + "; ".join(_describe_graphql_error(e) for e in fatal)
            )

        for error in denials:
            logger.warning(
                "GraphQL field denied by the token, continuing without it: %s",
                mask_secrets(_describe_graphql_error(error)),
            )

    def _request_with_retries(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        headers = self.auth.get_headers()
        delays = backoff_delays(self._max_retries, self._max_backoff)
        attempt = 0
        while True:
            try:
                response = self._client.request(method, url, headers=headers, **kwargs)
            except httpx.TransportError:
                if attempt >= self._max_retries:
                    raise
                self.sleep(delays[attempt])
                attempt += 1
                continue

            if response.status_code == 401:
                raise GitHubAuthError("Request was not authorized.")

            if response.status_code == 403 and _SSO_HEADER in response.headers:
                org, sso_url = _parse_sso_header(response.headers[_SSO_HEADER])
                raise GitHubSSOError(org=org, url=sso_url)

            if response.status_code in (403, 429) and self._is_secondary_rate_limit(response):
                retry_after = retry_after_seconds(response.headers.get("Retry-After"))
                if attempt >= self._max_retries:
                    raise SecondaryRateLimitError(retry_after)
                self.sleep(retry_after if retry_after is not None else delays[attempt])
                attempt += 1
                continue

            if response.status_code == 403:
                raise GitHubAuthError("Request was forbidden.")

            if response.status_code == 404:
                raise GitHubNotFoundError("Resource not found, or not visible to this token.")

            if response.status_code in _RETRYABLE_STATUSES:
                if attempt >= self._max_retries:
                    raise GitHubServerError(f"HTTP {response.status_code} after retries.")
                self.sleep(delays[attempt])
                attempt += 1
                continue

            if response.status_code >= 400:
                raise GitHubError(f"Unexpected HTTP {response.status_code}.")

            return response

    @staticmethod
    def _is_secondary_rate_limit(response: httpx.Response) -> bool:
        if response.status_code == 429:
            return True
        if "Retry-After" in response.headers:
            return True
        try:
            body = response.json()
        except ValueError:
            return False
        message = str(body.get("message", "")).lower()
        return "secondary rate limit" in message
