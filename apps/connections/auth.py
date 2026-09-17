"""The auth abstraction sync uses to talk to GitHub. auth_for_connection() is the only place
plaintext_token() is called for sync purposes."""

from dataclasses import dataclass
from typing import Protocol

from apps.connections.models import GitHubConnection
from apps.connections.services import plaintext_token


class ConnectionNotUsableError(Exception):
    """Raised when a connection cannot produce credentials to sync with (inactive or token-less)."""


class GitHubAuth(Protocol):
    def get_headers(self) -> dict[str, str]: ...

    def get_git_credentials(self) -> tuple[str, str]: ...

    @property
    def rate_limit_key(self) -> str: ...


@dataclass(frozen=True)
class PATAuth:
    """Serves both fine_grained_pat and classic_pat connection kinds."""

    connection_id: int
    _token: str

    def get_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
        }

    def get_git_credentials(self) -> tuple[str, str]:
        return ("x-access-token", self._token)

    @property
    def rate_limit_key(self) -> str:
        return f"connection:{self.connection_id}"

    def __repr__(self) -> str:
        return f"PATAuth(connection_id={self.connection_id})"

    def __str__(self) -> str:
        return self.__repr__()


def auth_for_connection(connection: GitHubConnection) -> GitHubAuth:
    if not connection.is_active:
        raise ConnectionNotUsableError(f"Connection {connection.pk} is not active.")
    if not connection.token_encrypted:
        raise ConnectionNotUsableError(f"Connection {connection.pk} has no token stored.")
    token = plaintext_token(connection)
    return PATAuth(connection_id=connection.pk, _token=token)
