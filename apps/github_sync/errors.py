"""Exception tree for the GitHub sync client, and the two shape-validation helpers.

require()/optional() error messages carry only the dotted JSON path, never the payload — a GitHub
response can legitimately contain a token-shaped string (e.g. pasted into a PR body), so quoting the
payload into an exception would route untrusted text into SyncRun.error_log."""

from typing import Any


class GitHubError(Exception):
    pass


class GitHubSchemaError(GitHubError):
    def __init__(self, path: str):
        self.path = path
        super().__init__(f"Missing or malformed field at path: {path}")


class GitHubAuthError(GitHubError):
    pass


class GitHubSSOError(GitHubError):
    def __init__(self, org: str, url: str):
        self.org = org
        self.url = url
        super().__init__(f"SSO authorization required for org {org}")


class GitHubServerError(GitHubError):
    pass


class SecondaryRateLimitError(GitHubError):
    def __init__(self, retry_after_seconds: float | None = None):
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Secondary rate limit hit")


class RateBudgetExhausted(GitHubError):
    def __init__(self, reset_at: Any):
        self.reset_at = reset_at
        super().__init__(f"Rate budget exhausted, resets at {reset_at}")


_MISSING = object()


def _walk(payload: Any, path: str) -> Any:
    """Walks a dotted/indexed path, returning _MISSING at the first segment that cannot be resolved."""
    current = payload
    consumed: list[str] = []
    for segment in path.split("."):
        consumed.append(segment)
        if "[" in segment:
            name, _, rest = segment.partition("[")
            index_str = rest.rstrip("]")
            if name:
                if not isinstance(current, dict) or name not in current:
                    return _MISSING, ".".join(consumed[:-1] + [name])
                current = current[name]
            try:
                index = int(index_str)
            except ValueError:
                return _MISSING, ".".join(consumed)
            if not isinstance(current, list) or index >= len(current) or index < 0:
                return _MISSING, ".".join(consumed)
            current = current[index]
        else:
            if not isinstance(current, dict) or segment not in current:
                return _MISSING, ".".join(consumed)
            current = current[segment]
    return current, path


def require(payload: Any, path: str) -> Any:
    value, resolved_path = _walk(payload, path)
    if value is _MISSING:
        raise GitHubSchemaError(resolved_path)
    return value


def optional(payload: Any, path: str, default: Any = None) -> Any:
    value, _resolved_path = _walk(payload, path)
    if value is _MISSING:
        return default
    return value
