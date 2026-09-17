"""The only two callers of apps/connections/crypto.py's decrypt/encrypt functions.

Nothing else in the codebase may call decrypt_token(); a test greps apps/ for that. plaintext_token()
returns to a local variable and must never be assigned to a model field, interpolated into a URL, or
put in an exception."""

import datetime
from typing import Any

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounts.services import record_audit
from apps.catalog.services import get_int
from apps.connections.crypto import decrypt_token, encrypt_token, token_last4
from apps.connections.models import GitHubConnection

User = get_user_model()

_WRITE_SCOPE = "repo"


def _parse_token_expiration_header(value: str | None) -> datetime.datetime | None:
    """GitHub sends 'github-authentication-token-expiration' as "YYYY-MM-DD HH:MM:SS UTC".
    Absent entirely for tokens with no expiry."""
    if not value:
        return None
    try:
        naive = datetime.datetime.strptime(value.removesuffix(" UTC").strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=datetime.UTC)


def set_token(connection: GitHubConnection, plaintext: str, *, actor: Any | None = None) -> None:
    connection.token_encrypted = encrypt_token(plaintext)
    connection.token_last4 = token_last4(plaintext)
    connection.save(update_fields=["token_encrypted", "token_last4", "updated_at"])
    record_audit(actor, "connection.token_set", connection, after={"token_last4": connection.token_last4})


def plaintext_token(connection: GitHubConnection) -> str:
    if not connection.token_encrypted:
        raise ValueError(f"Connection {connection.pk} has no token stored.")
    return decrypt_token(bytes(connection.token_encrypted))


def verify_connection(connection: GitHubConnection, *, force: bool = False) -> GitHubConnection:
    """Runs the spec §5.1 checks and stores codes + params only (never a rendered message).
    Throttled to once per CONNECTION_RECHECK_MIN_MINUTES unless force=True."""
    from apps.connections.auth import auth_for_connection
    from apps.github_sync.client import GitHubClient
    from apps.github_sync.errors import GitHubAuthError, GitHubSSOError, require
    from apps.github_sync.queries import (
        RATE_LIMIT_QUERY,
        REPOSITORIES_BY_OWNER_QUERY,
        VIEWER_REPOSITORIES_QUERY,
    )
    from apps.github_sync.rate_limit import RateBudget

    now = timezone.now()
    if not force and connection.last_checked_at is not None:
        elapsed = now - connection.last_checked_at
        if elapsed < datetime.timedelta(minutes=get_int("CONNECTION_RECHECK_MIN_MINUTES")):
            return connection

    auth = auth_for_connection(connection)
    client = GitHubClient(auth, RateBudget(key=auth.rate_limit_key))

    checks: list[dict[str, Any]] = []
    token_login = connection.token_login
    expires_at = connection.expires_at
    auth_ok = False

    try:
        user_body, user_headers = client.rest_get("/user")
    except GitHubAuthError:
        checks.append({"code": "AUTH_FAILED", "outcome": "fail", "params": {}})
    except GitHubSSOError as exc:
        checks.append(
            {
                "code": "SSO_AUTHORIZATION_REQUIRED",
                "outcome": "fail",
                "params": {"org": exc.org, "url": exc.url},
            }
        )
    else:
        auth_ok = True
        token_login = require(user_body, "login")
        checks.append({"code": "TOKEN_USER_OK", "outcome": "ok", "params": {"login": token_login}})
        expires_at = _parse_token_expiration_header(
            user_headers.get("github-authentication-token-expiration")
        )
        scopes_header = user_headers.get("X-OAuth-Scopes", "")
        scopes = [s.strip() for s in scopes_header.split(",") if s.strip()]
        if connection.kind == GitHubConnection.Kind.CLASSIC_PAT and _WRITE_SCOPE in scopes:
            checks.append(
                {"code": "CLASSIC_PAT_WRITE_SCOPE", "outcome": "fail", "params": {"scopes": scopes}}
            )

    if auth_ok:
        sso_blocked = False
        try:
            if connection.owner_login:
                data = client.graphql(
                    REPOSITORIES_BY_OWNER_QUERY,
                    {"login": connection.owner_login, "first": 5, "after": None},
                )
                repos = require(data, "repositoryOwner.repositories")
            else:
                data = client.graphql(VIEWER_REPOSITORIES_QUERY, {"first": 5, "after": None})
                repos = require(data, "viewer.repositories")
        except GitHubSSOError as exc:
            sso_blocked = True
            checks.append(
                {
                    "code": "SSO_AUTHORIZATION_REQUIRED",
                    "outcome": "fail",
                    "params": {"org": exc.org, "url": exc.url},
                }
            )
        else:
            count = require(repos, "totalCount")
            sample = [node["nameWithOwner"] for node in require(repos, "nodes")[:5]]
            checks.append(
                {"code": "REPOS_VISIBLE", "outcome": "ok", "params": {"count": count, "sample": sample}}
            )
            checks.append({"code": "PERM_PULL_REQUESTS", "outcome": "ok", "params": {}})

        repository = connection.repositories.filter(is_active=True).first()
        if repository is None:
            checks.append({"code": "PERM_CONTENTS_UNAVAILABLE", "outcome": "unavailable", "params": {}})
        else:
            try:
                client.rest_get(f"/repos/{repository.full_name}/contents/")
            except GitHubAuthError:
                checks.append({"code": "PERM_CONTENTS_DENIED", "outcome": "fail", "params": {}})
            else:
                checks.append({"code": "PERM_CONTENTS_OK", "outcome": "ok", "params": {}})

        if not sso_blocked:
            rate_data = client.graphql(RATE_LIMIT_QUERY, {})
            rate_limit = require(rate_data, "rateLimit")
            remaining = require(rate_limit, "remaining")
            reset_at_raw = require(rate_limit, "resetAt")
            checks.append(
                {
                    "code": "RATE_LIMIT",
                    "outcome": "ok",
                    "params": {"remaining": remaining, "reset_at": reset_at_raw},
                }
            )
            connection.rate_limit_remaining = remaining
            connection.rate_limit_reset_at = datetime.datetime.fromisoformat(
                reset_at_raw.replace("Z", "+00:00")
            )

    degraded_codes = {"SSO_AUTHORIZATION_REQUIRED", "CLASSIC_PAT_WRITE_SCOPE"}
    codes = {check["code"] for check in checks}
    if "AUTH_FAILED" in codes:
        status = GitHubConnection.Status.INVALID
    elif expires_at is not None and expires_at < now:
        status = GitHubConnection.Status.EXPIRED
    elif codes & degraded_codes:
        status = GitHubConnection.Status.DEGRADED
    else:
        status = GitHubConnection.Status.OK

    connection.status = status
    connection.token_login = token_login or ""
    connection.expires_at = expires_at
    connection.last_checked_at = now
    connection.last_check_result = {
        "checked_at": now.isoformat(),
        "checks": checks,
    }
    connection.save(
        update_fields=[
            "status",
            "token_login",
            "expires_at",
            "last_checked_at",
            "last_check_result",
            "rate_limit_remaining",
            "rate_limit_reset_at",
        ]
    )
    return connection
