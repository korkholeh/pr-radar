"""Secret masking shared by logging and (in a later phase) SyncRun.error_log."""

import re

_TOKEN_PREFIXES = ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_")
_TOKEN_RE = re.compile(rf"(?P<prefix>{'|'.join(_TOKEN_PREFIXES)})(?P<body>[A-Za-z0-9_]{{4,}})")


def mask_secrets(text: str) -> str:
    """Replace every GitHub token-shaped substring in ``text``, keeping the last 4 chars."""
    if not text:
        return text

    def _mask(match: re.Match[str]) -> str:
        prefix = match.group("prefix")
        body = match.group("body")
        last4 = body[-4:]
        return f"{prefix}{'*' * (len(body) - 4)}{last4}"

    return _TOKEN_RE.sub(_mask, text)
