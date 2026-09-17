"""Lazy message + hint per connection-check code (CLAUDE.md: never store a rendered English
message). verify_connection() stores only a code plus params; templates call render_message()/
render_hint() in the reader's language."""

from dataclasses import dataclass

from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class CheckCode:
    message: Promise
    hint: Promise


CHECK_CODES: dict[str, CheckCode] = {
    "TOKEN_USER_OK": CheckCode(
        message=_("The token authenticates as %(login)s."),
        hint=_("No action needed."),
    ),
    "AUTH_FAILED": CheckCode(
        message=_("GitHub rejected this token."),
        hint=_("Generate a new token and replace it on this connection."),
    ),
    "REPOS_VISIBLE": CheckCode(
        message=_("The token can see %(count)s repositories."),
        hint=_("No action needed."),
    ),
    "PERM_PULL_REQUESTS": CheckCode(
        message=_("The token can read pull requests."),
        hint=_("No action needed."),
    ),
    "PERM_CONTENTS_OK": CheckCode(
        message=_("The token can read repository contents."),
        hint=_("No action needed."),
    ),
    "PERM_CONTENTS_DENIED": CheckCode(
        message=_("The token cannot read repository contents."),
        hint=_("Grant the token read access to repository contents and re-check."),
    ),
    "PERM_CONTENTS_UNAVAILABLE": CheckCode(
        message=_("Repository contents access was not checked."),
        hint=_("Add a repository to this connection, then re-check."),
    ),
    "SSO_AUTHORIZATION_REQUIRED": CheckCode(
        message=_("Organization %(org)s requires SSO authorization for this token."),
        hint=_("Authorize the token for single sign-on at %(url)s."),
    ),
    "CLASSIC_PAT_WRITE_SCOPE": CheckCode(
        message=_("The classic token carries the full 'repo' scope, which grants write access."),
        hint=_("Replace it with a fine-grained token limited to the repositories PR Radar reads."),
    ),
    "RATE_LIMIT": CheckCode(
        message=_("%(remaining)s API requests remain until %(reset_at)s."),
        hint=_("No action needed."),
    ),
}


def render_message(code: str, params: dict) -> str:
    return str(CHECK_CODES[code].message) % params


def render_hint(code: str, params: dict) -> str:
    return str(CHECK_CODES[code].hint) % params
