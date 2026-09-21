"""Repository discovery over the REST API.

GraphQL's `viewer { repositories }` is not a dependable list for a fine-grained token. A token whose
resource owner is an organization authenticates as the user but carries no viewer affiliation with
that organization's repositories, so the GraphQL connection comes back short -- often empty -- while
the very same token reads those repositories over REST without complaint. Discovery therefore asks
REST, from two directions:

* `/user/repos` -- everything the account itself is affiliated with (owner, collaborator, member).
* `/orgs/{login}/repos` -- per organization the token can see, which is where an organization-owned
  fine-grained token's grants actually surface.

Both are merged and keyed by `node_id`, and each row is reshaped into the same node dict the GraphQL
document used to return (`id`, `name`, `nameWithOwner`, `isPrivate`, `isArchived`, `defaultBranchRef`,
`owner`), so catalog, views and templates downstream are unchanged.

Denials are tolerated wherever they can be: `/user/orgs` and a single organization's repository list
may answer 403/404 for a token that simply was not granted them, and so may `/user/repos` itself -- a
token can be refused the account-level listing and still read an organization's repositories. A
denial on `/user/repos` is only raised when the organization sweep also came back empty, so nothing
is listed and the caller has to hear about it. An SSO challenge always propagates: it is actionable,
and verify_connection() turns it into SSO_AUTHORIZATION_REQUIRED."""

import logging
from collections.abc import Iterable, Iterator
from typing import Any, Protocol

from apps.github_sync.errors import (
    GitHubAuthError,
    GitHubError,
    GitHubNotFoundError,
    optional,
    require,
)

logger = logging.getLogger(__name__)

# affiliation is spelled out rather than left to GitHub's default so the query says what it means:
# a lead working on a client-owned repository is a collaborator, never the owner. Archived
# repositories are listed too -- the discovery page has its own filter for them.
USER_REPOS_PATH = "/user/repos?affiliation=owner,collaborator,organization_member&sort=full_name"
USER_ORGS_PATH = "/user/orgs"


class _Client(Protocol):
    def rest_paginate(self, path: str, *, page_size: int) -> Iterator[dict]: ...


def repository_node(payload: dict) -> dict:
    """One REST repository object in the shape of the GraphQL node discovery used to yield.

    `node_id` is the repository's GraphQL global id, so `Repository.github_id` keeps matching rows
    written before discovery moved to REST. `owner.type` ("Organization"/"User") lands in
    `__typename`, which is the value catalog compares against."""
    owner = require(payload, "owner")
    default_branch = optional(payload, "default_branch", "")
    return {
        "id": require(payload, "node_id"),
        "name": require(payload, "name"),
        "nameWithOwner": require(payload, "full_name"),
        "isPrivate": require(payload, "private"),
        # A repository GitHub has never archived may omit the key entirely on some REST shapes.
        "isArchived": bool(optional(payload, "archived", False)),
        "defaultBranchRef": {"name": default_branch} if default_branch else None,
        "owner": {
            "id": require(owner, "node_id"),
            "login": require(owner, "login"),
            "__typename": require(owner, "type"),
        },
    }


def _org_logins(client: _Client, *, page_size: int, extra: Iterable[str] = ()) -> list[str]:
    logins: list[str] = []
    try:
        for org in client.rest_paginate(USER_ORGS_PATH, page_size=page_size):
            logins.append(require(org, "login"))
    except (GitHubAuthError, GitHubNotFoundError):
        # A fine-grained token needs an explicit organization-read permission to list memberships.
        # Without it the sweep falls back to whatever logins the caller passed in.
        logger.info("Token cannot list organization memberships; using the connection's owner only.")
    for login in extra:
        if login and login not in logins:
            logins.append(login)
    return logins


def repository_nodes(
    client: _Client, *, page_size: int, owner_logins: Iterable[str] = ()
) -> list[dict[str, Any]]:
    """Every repository the token can read, whoever owns it, deduplicated by GraphQL node id.

    owner_logins are organizations to sweep in addition to the ones `/user/orgs` reports -- the
    connection's own owner_login, which is the one organization a lead is sure to care about even
    when the token may not list its memberships."""
    nodes: dict[str, dict[str, Any]] = {}
    account_denial: GitHubError | None = None
    try:
        for payload in client.rest_paginate(USER_REPOS_PATH, page_size=page_size):
            node = repository_node(payload)
            nodes.setdefault(node["id"], node)
    except (GitHubAuthError, GitHubNotFoundError) as exc:
        # A token may be denied the account-level listing and still read an organization's
        # repositories, which is the whole shape this module exists for. The denial is only fatal if
        # the organization sweep then finds nothing either -- otherwise it would throw away the very
        # list the caller asked for.
        account_denial = exc

    for login in _org_logins(client, page_size=page_size, extra=owner_logins):
        try:
            payloads = list(client.rest_paginate(f"/orgs/{login}/repos?type=all", page_size=page_size))
        except (GitHubAuthError, GitHubNotFoundError):
            logger.info("Token cannot list repositories for organization %s; skipping it.", login)
            continue
        for payload in payloads:
            node = repository_node(payload)
            nodes.setdefault(node["id"], node)

    if account_denial is not None and not nodes:
        raise account_denial
    return list(nodes.values())
