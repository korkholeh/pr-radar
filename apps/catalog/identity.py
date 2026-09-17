"""Identity resolution (spec §4.1): collapses GitHub logins and git emails into `Person`
records, detects bots and merges duplicate people. A GitHub login identifies exactly one GitHub
account, so it is always auto-mapped; a bare git email is not — it is only adopted when GitHub
itself links it to a login (a noreply address, or a commit that pairs the email with a login).
Anything else lands in the unmapped-identity queue for a lead to resolve."""

import re

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Identity, Person
from apps.catalog.services import get_list

_NOREPLY_RE = re.compile(
    r"^(?:\d+\+)?(?P<login>[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)@users\.noreply\.github\.com$",
    re.IGNORECASE,
)


def is_bot_login(login: str) -> bool:
    normalized = login.strip().casefold()
    suffixes = [s.casefold() for s in get_list("BOT_LOGIN_SUFFIXES")]
    exact = {s.casefold() for s in get_list("BOT_LOGINS")}
    if normalized in exact:
        return True
    return any(normalized.endswith(suffix) for suffix in suffixes)


def login_from_noreply_email(email: str) -> str | None:
    match = _NOREPLY_RE.match(email.strip())
    return match.group("login") if match else None


def person_for_login(login_identity: Identity) -> Person:
    """Creates (or returns) the `Person` a `github_login` identity resolves to. Never touches an
    already-mapped identity — that call is the caller's, so a lead's assignment always wins."""
    if login_identity.person is not None:
        return login_identity.person
    person = Person.objects.create(
        display_name=login_identity.value, is_bot=is_bot_login(login_identity.value)
    )
    login_identity.person = person
    login_identity.save(update_fields=["person"])
    return person


def create_person_from_identity(
    identity: Identity, *, is_bot: bool = False, exclude_from_metrics: bool = False
) -> Person:
    """Settings → People UI action: attaches a fresh `Person` to an unmapped identity (used by
    "create person", "mark bot" and "exclude" queue actions). A no-op on an already-mapped
    identity, same guard as `resolve_identity()`."""
    if identity.person is not None:
        return identity.person
    person = Person.objects.create(
        display_name=identity.value, is_bot=is_bot, exclude_from_metrics=exclude_from_metrics
    )
    identity.person = person
    identity.save(update_fields=["person"])
    return person


def _commit_linked_login_identity(email_identity: Identity) -> Identity | None:
    """The `Commit` row(s) GitHub itself paired this email with, via `author_email_identity`
    (§4 of the plan) — the second auto-mapping rule, a GitHub-linked commit email."""
    from apps.activity.models import Commit

    commit = (
        Commit.objects.filter(author_email_identity=email_identity, author_identity__isnull=False)
        .exclude(author_identity=email_identity)
        .select_related("author_identity")
        .first()
    )
    return commit.author_identity if commit else None


def resolve_identity(identity: Identity) -> Identity:
    """Idempotent single-row resolution: a no-op once `person` is set, so re-running it (e.g. a
    second sync, `recompute`) never re-points an identity a lead has already assigned."""
    if identity.person is not None:
        return identity

    if identity.kind == Identity.Kind.GITHUB_LOGIN:
        person_for_login(identity)
        return identity

    if identity.kind == Identity.Kind.GIT_EMAIL:
        login = login_from_noreply_email(identity.value)
        if login is not None:
            login_identity, _created = Identity.objects.get_or_create(
                kind=Identity.Kind.GITHUB_LOGIN, value=login, defaults={"person": None}
            )
            resolve_identity(login_identity)
            identity.person = login_identity.person
            identity.save(update_fields=["person"])
            return identity

        linked_login_identity = _commit_linked_login_identity(identity)
        if linked_login_identity is not None:
            resolve_identity(linked_login_identity)
            identity.person = linked_login_identity.person
            identity.save(update_fields=["person"])
            return identity

    return identity


def resolve_identities_for_pull_request(pull_request_id: int) -> None:
    """The pipeline entry point (spec §5.3 step 4): resolves the PR's author, `merged_by`, every
    review/review-comment author and every commit author/committer/email identity. O(identities)
    per PR, bounded, and a no-op on a second pass since every step is `person is None`-guarded."""
    from apps.activity.models import PullRequest

    pull_request = PullRequest.objects.select_related("author", "merged_by").get(pk=pull_request_id)

    identity_ids: set[int] = set()
    if pull_request.author_id is not None:
        identity_ids.add(pull_request.author_id)
    if pull_request.merged_by_id is not None:
        identity_ids.add(pull_request.merged_by_id)

    identity_ids.update(pull_request.reviews.exclude(reviewer=None).values_list("reviewer_id", flat=True))
    identity_ids.update(pull_request.review_comments.exclude(author=None).values_list("author_id", flat=True))

    commit_ids = pull_request.pull_request_commits.values_list("commit_id", flat=True)
    from apps.activity.models import Commit

    for field in ("author_identity_id", "committer_identity_id", "author_email_identity_id"):
        identity_ids.update(
            Commit.objects.filter(pk__in=commit_ids).exclude(**{field: None}).values_list(field, flat=True)
        )

    for identity in Identity.objects.filter(pk__in=identity_ids):
        resolve_identity(identity)


@transaction.atomic
def merge_people(source: Person, target: Person, actor: User | None) -> Person:
    """Re-points every `Identity` of `source` to `target`, keeps `target`'s flags, appends
    `source.notes` and deletes `source`. `PullRequest.author` points at `Identity`, never at
    `Person`, so no PR row is touched and none can be orphaned."""
    from apps.accounts.services import record_audit

    if source.pk == target.pk:
        raise ValidationError(_("Cannot merge a person into themselves."))

    identity_ids = list(source.identities.values_list("pk", flat=True))
    source.identities.update(person=target)

    if source.notes:
        target.notes = f"{target.notes}\n\n{source.notes}".strip() if target.notes else source.notes
        target.save(update_fields=["notes"])

    record_audit(
        actor,
        "person.merge",
        target,
        before={"source": source.display_name, "identities": identity_ids},
        after={"target": target.display_name},
    )
    source.delete()
    return target
