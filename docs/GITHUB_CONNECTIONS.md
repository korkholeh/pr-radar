# GitHub connections and sync

A **connection** is one GitHub credential PR Radar syncs through. Each repository belongs to exactly one
connection at a time. This page covers token types, the first-run checklist, what each verification code means,
key rotation, recovery, rebinding, and the `.env` bootstrap shortcut. For per-day/per-run tuning knobs
(`SYNC_OVERLAP_MINUTES`, `SYNC_PR_PAGE_SIZE`, …) see `docs/CONFIGURATION.md`.

## Token types and required scopes

| Kind | Scope | Notes |
|---|---|---|
| Fine-grained personal access token (recommended) | Read-only access to **Pull requests**, **Contents**, **Metadata** on the repositories/owner you choose | Issued per resource owner, so `owner_login` on the connection should match. Supports organization SSO authorization. |
| Classic personal access token | `repo` (read) **or**, if your org disallows scoped fine-grained tokens, the narrowest classic scope that still reads private repositories | A classic token that carries the full `repo` scope also grants **write** access even though PR Radar never uses it — verification flags this (`CLASSIC_PAT_WRITE_SCOPE`) because the token is more powerful than the tool needs. |
| GitHub App | — | Reserved by the `GitHubAuth` protocol (`app_id`/`installation_id`/`private_key_encrypted` on `GitHubConnection`); not implemented in v1. |

Both PAT kinds must be able to read: pull requests, reviews, review comments, commits, check runs, and files
changed, on every repository the connection will sync.

## First-run checklist

1. Set at least one key in `FIELD_ENCRYPTION_KEYS` (`.env`) — a connection cannot be saved without one; saving
   with an empty list raises rather than storing the token unencrypted.
2. **Settings → Connections → New connection.** Pick a name, a kind, and (for a fine-grained token) the owner
   login. Paste the token and save — this verifies immediately and refuses to save an invalid token unless you
   explicitly tick "save without verifying".
3. **Settings → Repositories.** Pick the connection, review the repositories it can see (grouped by owner,
   archived ones hidden by default), tick the ones to sync, optionally assign a project, and add them. Nothing is
   persisted until you submit a selection.
4. **Sync → Sync now**, or `uv run python manage.py sync`.

## Verification codes

Each connection stores its last check as a list of codes plus parameters — never a rendered message — so the
UI can translate it. This is every code `verify_connection()` can emit and what to do about it:

| Code | Meaning | What to do |
|---|---|---|
| `TOKEN_USER_OK` | The token authenticates and GitHub returned a login. | Nothing. |
| `AUTH_FAILED` | GitHub rejected the token (expired, revoked, or never valid). Sets the connection to `invalid`. | Generate a new token and replace it on this connection. |
| `REPOS_VISIBLE` | The token can see at least one repository through this connection's owner (or, for a token with no single owner, through the viewer). | Nothing. |
| `PERM_PULL_REQUESTS` | The token can read pull requests. | Nothing. |
| `PERM_CONTENTS_OK` | The token can read repository contents, checked against one of the connection's active repositories. | Nothing. |
| `PERM_CONTENTS_DENIED` | The token cannot read repository contents on the checked repository. | Grant the token read access to repository contents and re-check. |
| `PERM_CONTENTS_UNAVAILABLE` | Contents access has not been checked yet — no repository is added to this connection to test against. | Add a repository to this connection, then re-check. |
| `SSO_AUTHORIZATION_REQUIRED` | The organization requires SSO authorization for this token. Sets the connection to `degraded`. | Authorize the token for single sign-on at the URL GitHub returned (shown in the UI). |
| `CLASSIC_PAT_WRITE_SCOPE` | A classic token carries the full `repo` scope, which is write access PR Radar never needs. Sets the connection to `degraded`. | Replace it with a fine-grained token limited to the repositories PR Radar reads. |
| `RATE_LIMIT` | Current primary rate-limit remaining and reset time. | Nothing, unless remaining is unexpectedly low — check for another process using the same token. |

Status derivation: any `AUTH_FAILED` → `invalid`; an expired `expires_at` → `expired`; an `SSO_AUTHORIZATION_REQUIRED`
or `CLASSIC_PAT_WRITE_SCOPE` with no `AUTH_FAILED` → `degraded`; otherwise `ok`.

Verification is throttled to once per `CONNECTION_RECHECK_MIN_MINUTES` (default 60) per connection; the **Check**
button in the UI always forces an immediate re-check. A banner warns admins about `invalid`/`expired` connections
and any connection expiring within `TOKEN_EXPIRY_WARNING_DAYS` (default 14) days.

## Rotating `FIELD_ENCRYPTION_KEYS`

To rotate without downtime:

1. Add the new key at **position 0** of `FIELD_ENCRYPTION_KEYS`, keeping the old key(s) after it.
2. Run `uv run python manage.py rotate_encryption_key` (add `--dry-run` first to see how many rows would move,
   with no writes). This re-encrypts every stored token under the new key #0, inside one transaction.
3. Once it reports every row moved, remove the old key from `FIELD_ENCRYPTION_KEYS`.

Running `rotate_encryption_key` again after a successful rotation is a no-op — every token is already encrypted
under key #0.

## Recovery when `FIELD_ENCRYPTION_KEYS` is lost

There is no recovery: a lost key makes every token it alone encrypted permanently undecryptable, by design — the
whole point of encryption at rest is that no one without the key can read it, including the tool itself. Treat
`FIELD_ENCRYPTION_KEYS` as part of your backup (see `docs/SETUP.md`'s backup section) and back it up separately
from `DATA_DIR`, since the two together are what an attacker with only the database would need. If a key is
lost, replace the affected connections' tokens: **Settings → Connections → Edit**, paste a fresh token, save.

## Rebinding a repository

A repository's connection can change without losing its history: on **Settings → Repositories**, a repository
already bound to a different connection shows that connection's name and a **Rebind here** control with an
explicit confirmation checkbox. Rebinding only changes which credentials future syncs use — every pull request,
review, commit and file already synced stays exactly as it is.

Deleting a connection that still has repositories is refused (`Repository.connection` is a protected foreign
key); the UI shows which repositories block the delete so you can rebind or deactivate them first.

## The `.env` bootstrap path

For a first connection only, `uv run python manage.py bootstrap_connection` reads `GITHUB_TOKEN` from the
environment and creates a connection named "Default (.env)" from it, left `unverified` — it makes no GitHub call
by itself. Add `--verify` to verify it immediately after creating it. If any connection already exists, the
command does nothing and logs a warning naming `GITHUB_TOKEN` instead of creating a second one silently. This
path exists for scripted/first-run setups; day-to-day connection management is the Settings UI.
