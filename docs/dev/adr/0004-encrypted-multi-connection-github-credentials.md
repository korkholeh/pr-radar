# 0004. Store GitHub tokens as Fernet ciphertext in the database, one row per connection, behind a `GitHubAuth` protocol

- **Status:** accepted
- **Date:** 2026-09-17

## Context

Repositories belong to several organisations, including client organisations (spec §5.1), so there is more than
one credential. Fine-grained PATs are issued per resource owner, which makes "one token" impossible by
construction. The spec therefore models each credential as a `GitHubConnection` row managed by an admin in the UI.

The token is the only secret in this system whose leak cannot be undone by the operator alone: it grants read
access to a client's private source code. Spec §11 and §12 treat it accordingly — the token must not appear in
the database in plaintext, in logs, in `SyncRun.error_log`, in exceptions, in audit entries, in exports, in
Django admin, in templates, or in any HTTP response; only `…last4` is ever shown; it can be replaced but never
read back; and a test must go looking for it in all of those places after a failing sync and after an export.

Spec §2 also requires that a future GitHub App can be added without changing the sync code.

## Decision

**At rest.** `GitHubConnection.token_encrypted` is a `BinaryField` holding Fernet ciphertext.
`FIELD_ENCRYPTION_KEYS` in `.env` is a comma-separated list; the first key encrypts and all keys decrypt, via
`cryptography.fernet.MultiFernet`. `manage.py rotate_encryption_key` re-encrypts every token with the first key.
The keys are never stored in the database — an exfiltrated `db.sqlite3` yields no tokens.

**In memory.** Decryption happens in one accessor in `apps/connections`, returning the plaintext to a local
variable for the duration of one request or task. The plaintext is never assigned to a model field, never
interpolated into a URL, never placed in a subprocess argument, and never put into an exception message.

**In transit to `git`.** `churn` receives credentials through `GitHubAuth.get_git_credentials()` and hands them to
`git` via a `GIT_ASKPASS` helper reading a process environment variable. Never in the remote URL, never in
`.git/config`, never in a credential store, never in `ps` output.

**In logs.** A `SecretMaskingFilter` is attached to the root logger in phase 0 — before any token exists — and
redacts `ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_` and `github_pat_` shapes from every record. `SyncRun.error_log` is
written through the same masking function.

**In the UI.** The form accepts a token and shows `token_last4` afterwards. There is no "reveal" action. The
Django admin registration for `GitHubConnection` omits the token field entirely and marks the rest read-only.
Creating, replacing, deactivating or deleting a connection writes an `AuditEntry` — without the token.

**The abstraction.** Sync and churn depend on the protocol, not on the model:

```python
class GitHubAuth(Protocol):
    def get_headers(self) -> dict[str, str]: ...
    def get_git_credentials(self) -> tuple[str, str]: ...
    @property
    def rate_limit_key(self) -> str: ...
```

`fine_grained_pat` and `classic_pat` implement it in v1. `github_app` is reserved: the nullable `app_id`,
`installation_id` and `private_key_encrypted` fields exist so that adding it later is a third implementation of
this protocol and nothing else.

**Bootstrap.** If `GITHUB_TOKEN` is set in `.env` and no connection exists, `manage.py bootstrap_connection`
creates one named "Default (.env)". From then on the database is the source of truth, and a warning is logged
while the variable remains. *For this run the path is implemented and unit-tested but never executed against the
real API — no token exists (intake).*

## Alternatives considered

- **A single token in `.env`** — rejected: fine-grained PATs are per resource owner, so it cannot cover several
  client organisations, and rotating or replacing one would need a file edit and a restart.
- **`django-fernet-fields` or a similar packaged encrypted field** — rejected: an unmaintained-dependency risk on
  the one thing that must not break, for about forty lines of `cryptography` calls that we must test ourselves
  regardless.
- **OS keychain (macOS Keychain / `keyring`)** — rejected: it ties the data directory to one machine and one user
  account, breaks the "copy `DATA_DIR` to back up" story, and cannot work on the eventual server (§13).
- **Hashing instead of encrypting** — impossible: the token must be replayed to GitHub.
- **Storing the token in plaintext and relying on file permissions** — rejected: the spec forbids it, and a
  SQLite file gets copied, synced and attached to bug reports.
- **A GitHub App in v1** — out of scope; preserved by the protocol above.

## Consequences

- **Buys:** a database file that is safe to copy and back up, per-organisation credential isolation (a 401 on one
  client's token quarantines only that client's repositories), and key rotation without re-entering tokens.
- **Costs:** losing every value in `FIELD_ENCRYPTION_KEYS` makes the tokens unrecoverable. This is the intended
  trade: the recovery procedure is "re-enter the tokens", documented in `docs/GITHUB_CONNECTIONS.md`, and no other
  data is affected.
- **Harder:** any new code path that touches a connection must go through the accessor. The leak test from spec
  §12 — search the DB, the logs, `SyncRun.error_log`, rendered HTML and export files for the token after a failing
  sync — is what makes a mistake visible rather than theoretical.
- **Revisit when:** GitHub App support lands (a new `GitHubAuth` implementation), or the tool moves to a server
  with a real secret manager (`FIELD_ENCRYPTION_KEYS` comes from the manager; nothing else changes).
