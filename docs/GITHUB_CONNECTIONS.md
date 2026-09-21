# GitHub connections and sync

A **connection** is one GitHub credential PR Radar syncs through. Each repository belongs to exactly one
connection at a time. This page covers token types, the first-run checklist, what each verification code means,
key rotation, recovery, rebinding, and the `.env` bootstrap shortcut. For per-day/per-run tuning knobs
(`SYNC_OVERLAP_MINUTES`, `SYNC_PR_PAGE_SIZE`, …) see `docs/CONFIGURATION.md`.

## Token types and required scopes

| Kind | Scope | Notes |
|---|---|---|
| Fine-grained personal access token (recommended) | Read-only access to **Pull requests**, **Contents**, **Metadata** on the repositories/owner you choose | Issued per resource owner. `owner_login` on the connection is only a label — discovery lists every repository the token can read, whoever owns it. Supports organization SSO authorization. |
| Classic personal access token | `repo` for private repositories; `public_repo` reads public ones only | A classic token that carries the full `repo` scope also grants **write** access even though PR Radar never uses it — verification flags this (`CLASSIC_PAT_WRITE_SCOPE`) because the token is more powerful than the tool needs. A classic token *without* `repo` cannot read private repositories at all, which verification flags as `CLASSIC_PAT_NO_PRIVATE_SCOPE`. |
| GitHub App | — | Reserved by the `GitHubAuth` protocol (`app_id`/`installation_id`/`private_key_encrypted` on `GitHubConnection`); not implemented in v1. |

Both PAT kinds must be able to read: pull requests, reviews, review comments, commits, check runs, and files
changed, on every repository the connection will sync.

**Checks** is the one permission a sync can do without. A fine-grained token that lacks it still reads every
pull request, review and commit; GitHub refuses the `statusCheckRollup` field alone, and PR Radar logs that
denial and stores the commit without a check state. Metrics that read check status (the quality-gate policy
rules) simply have nothing to read for those commits. Add **Checks: read** to the token if you want them.
A refusal on the *repository* itself is a different matter: that means the token may not read the repository
at all, and it sets the connection to `invalid` and stops the run for it.

## First-run checklist

1. Set at least one key in `FIELD_ENCRYPTION_KEYS` (`.env`) — a connection cannot be saved without one; saving
   with an empty list raises rather than storing the token unencrypted.
2. **Settings → Connections → New connection.** Pick a name, a kind, and (for a fine-grained token) the owner
   login. Paste the token and save — this verifies immediately and refuses to save an invalid token unless you
   explicitly tick "save without verifying".
3. **Settings → Repositories.** Pick the connection, review the repositories it can see (grouped by owner,
   archived ones hidden by default), tick the ones to sync, optionally assign a project, and add them. Nothing is
   persisted until you submit a selection. The list is everything the token can read — repositories the account
   owns, ones it collaborates on (a client's repository, say), and ones in organizations it belongs to — so an
   **Owner** filter is offered when more than one owner shows up. Ownership is never a condition for listing.
4. **Sync → Sync now**, or `uv run python manage.py sync`. To reach further back than the last run —
   a repository added after the fact, or a window that turned out too narrow — use **Sync → Load historical
   data** (a preset window or a date of your own, all active repositories or only the ones you pick), or
   `uv run python manage.py sync --since YYYY-MM-DD`. A backfill takes the same lock and rate-limit budget as
   an ordinary sync, so the two never overlap.
5. Budget the rate limit for that first sync — see "Rate-limit budget" below — and afterwards check
   **Settings → Connections** shows `ok` (or a known, already-understood `degraded`); if it shows anything else,
   look up the code under "Verification codes" below and re-check once you've acted on it.

## Rate-limit budget

GitHub's GraphQL API meters primary rate limit in points, not requests: **5,000 points/hour** for an
authenticated token (both PAT kinds get the same budget). Nested connections cost more per pull request — a PR
with many reviews, commits and files is pricier to fetch than a small one — so the exact number of PRs a full
hourly budget covers varies by repository. `RATE_LIMIT_MIN_REMAINING` (default 200, see `docs/CONFIGURATION.md`)
is the remaining-points floor below which the client pauses and waits for GitHub's hourly reset instead of
pushing through and risking a hard 403.

Discovery does not spend that budget: listing repositories runs over the REST API, which is metered separately
(5,000 requests/hour). Opening **Settings → Repositories** for a connection walks every repository the token can
read, 100 per request, and re-walks them when you change the owner filter or the archived toggle. It asks
`/user/repos` plus one `/orgs/{login}/repos` per organization the token can see, so a lead in several
organizations costs a handful of requests per page load.

Listing is REST rather than GraphQL on purpose: GraphQL's `viewer { repositories }` leaves out the repositories a
fine-grained token whose **resource owner is the organization** was granted, because the token authenticates as
the user and the user has no viewer affiliation with them. Such a token reads those repositories perfectly well —
it just never appeared in the GraphQL list, which showed up as an empty discovery page.

The **first** sync on a connection is the most expensive one: it walks `BACKFILL_DAYS` (default 180) of history
for every repository you added. A connection with many repositories, or a long backfill window, can spend its
whole hourly budget before finishing one pass — expect a large first sync to pause and resume across more than
one hour, which is normal, not a stuck sync. The `RATE_LIMIT` code (below) reports the remaining budget and reset
time after every check; the Sync page's run history shows the same numbers per connection once a sync has run.

## Verification codes

Each connection stores its last check as a list of codes plus parameters — never a rendered message — so the
UI can translate it. This is every code `verify_connection()` can emit and what to do about it:

| Code | Meaning | What to do |
|---|---|---|
| `TOKEN_USER_OK` | The token authenticates and GitHub returned a login. | Nothing. |
| `AUTH_FAILED` | GitHub rejected the token (expired, revoked, or never valid). Sets the connection to `invalid`. | Generate a new token and replace it on this connection. |
| `REPOS_VISIBLE` | The token can see at least one repository, counted over the same REST listing discovery uses — across every owner it has access to. | Nothing. |
| `REPOS_VISIBLE_NONE` | The token can see no repository at all, so nothing can be discovered or synced through it. Sets the connection to `degraded`. | Grant the token read access to the repositories you track: select them explicitly on a fine-grained token, and authorize single sign-on if the organization requires it. |
| `PERM_PULL_REQUESTS` | The token can read pull requests, checked by actually listing them on one of the connection's active repositories. | Nothing. |
| `PERM_PULL_REQUESTS_DENIED` | The checked repository answered 403 or 404 — the token cannot read its pull requests. Sets the connection to `degraded`. | Grant read access to pull requests on that repository and re-check. A private repository needs more than a classic token's `public_repo` scope. |
| `PERM_PULL_REQUESTS_UNAVAILABLE` | Pull request access has not been checked — no repository is added to this connection to test against. | Add a repository to this connection, then re-check. |
| `PERM_CONTENTS_OK` | The token can read repository contents, checked against one of the connection's active repositories. | Nothing. |
| `PERM_CONTENTS_DENIED` | The token cannot read repository contents on the checked repository. | Grant the token read access to repository contents and re-check. |
| `PERM_CONTENTS_UNAVAILABLE` | Contents access has not been checked yet — no repository is added to this connection to test against. | Add a repository to this connection, then re-check. |
| `SSO_AUTHORIZATION_REQUIRED` | The organization requires SSO authorization for this token. Sets the connection to `degraded`. | Authorize the token for single sign-on at the URL GitHub returned (shown in the UI). |
| `CLASSIC_PAT_WRITE_SCOPE` | A classic token carries the full `repo` scope, which is write access PR Radar never needs. Sets the connection to `degraded`. | Replace it with a fine-grained token limited to the repositories PR Radar reads. |
| `CLASSIC_PAT_NO_PRIVATE_SCOPE` | A classic token lacks `repo` while the connection tracks at least one **private** repository, so it can read none of them. Sets the connection to `degraded`. | Replace it with a fine-grained token that has read access to those repositories, or add the `repo` scope to this one. |
| `RATE_LIMIT` | Current primary rate-limit remaining and reset time. | Nothing, unless remaining is unexpectedly low — check for another process using the same token. |

Status derivation: any `AUTH_FAILED` → `invalid`; an expired `expires_at` → `expired`; an `SSO_AUTHORIZATION_REQUIRED`,
`CLASSIC_PAT_WRITE_SCOPE`, `CLASSIC_PAT_NO_PRIVATE_SCOPE`, `REPOS_VISIBLE_NONE` or `PERM_PULL_REQUESTS_DENIED`
with no `AUTH_FAILED` → `degraded`; otherwise `ok`.

**Why a scope problem is worth its own check.** GitHub does not report one. Asked for a private repository it may
not read, a `public_repo` classic token gets a GraphQL response containing the repository — with
`viewerPermission` still reporting the caller's org role, and every connection on it empty: zero branches, zero
pull requests, no `errors` array. REST is blunter and returns 404, because GitHub will not confirm that a private
repository exists to a token that cannot see it. A sync therefore completes as `success` with 0 pull requests and
nothing else to show for it, which is why both probes above run against a real repository and treat 404 the same
as 403.

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

A repository's connection can change without losing its history, from either of two places. **Settings →
Repository settings** lists every repository already added with the connection that syncs it; **Change
connection** opens a form offering every other active connection, and the move needs an explicit confirmation.
**Settings → Discover repositories** covers the other direction: a repository the chosen connection can see but
that is bound elsewhere shows that connection's name and a **Rebind here** control, also behind a confirmation.
Both go through `rebind_repository()` and write a `repository.rebind` audit entry.

Rebinding only changes which credentials future syncs use — every pull request, review, commit and file already
synced stays exactly as it is. Nothing checks that the new token can actually read the repository, so verify the
connection afterwards if you are not sure; a token that cannot read it fails at the next sync, not at the move.

Replacing a token on an existing connection (**Settings → Connections → Edit**) needs no rebinding at all — the
repositories keep pointing at the same connection. Rebinding is for moving repositories onto a *different*
connection, such as one issued for a different owner or with a narrower repository selection.

Deleting a connection that still has repositories is refused (`Repository.connection` is a protected foreign
key); the UI shows which repositories block the delete so you can rebind or deactivate them first.

## The `.env` bootstrap path

For a first connection only, `uv run python manage.py bootstrap_connection` reads `GITHUB_TOKEN` from the
environment and creates a connection named "Default (.env)" from it, left `unverified` — it makes no GitHub call
by itself. Add `--verify` to verify it immediately after creating it. If any connection already exists, the
command does nothing and logs a warning naming `GITHUB_TOKEN` instead of creating a second one silently. This
path exists for scripted/first-run setups; day-to-day connection management is the Settings UI.
