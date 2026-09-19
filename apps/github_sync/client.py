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
    GitHubServerError,
    GitHubSSOError,
    GitHubWriteAttemptError,
    SecondaryRateLimitError,
)
from apps.github_sync.errors import require as require_path
from apps.github_sync.rate_limit import RateBudget, backoff_delays, retry_after_seconds

logger = logging.getLogger(__name__)

_RETRYABLE_STATUSES = (502, 503, 504)
_SSO_HEADER = "X-GitHub-SSO"


def _parse_iso8601(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


_WRITE_KEYWORDS = ("mutation", "subscription")


def assert_read_only(document: str) -> None:
    """Refuses any GraphQL document that is not a plain query. PR Radar reads GitHub and never
    writes to it (ADR 0003, and the read-only PAT in the architecture diagram). REST is read-only
    by construction -- `rest_get()` is the only REST entry point -- but GraphQL sends reads and
    writes alike as a POST, so nothing about the request itself carries the promise. This puts it
    on the document: every operation definition must be a `query`.

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
            self._raise_for_graphql_errors(errors)
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

    def _raise_for_graphql_errors(self, errors: Iterable[dict]) -> None:
        for error in errors:
            if error.get("type") in ("FORBIDDEN", "UNAUTHORIZED"):
                raise GitHubAuthError("GraphQL request was not authorized.")
        raise GitHubError("; ".join(str(e.get("message", "unknown error")) for e in errors))

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
