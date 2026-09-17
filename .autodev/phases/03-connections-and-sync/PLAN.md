# Phase 3 — GitHub connections and incremental sync

**Goal:** encrypted multi-connection credentials, a fixture-tested GraphQL/REST client, repository discovery,
and an idempotent incremental sync driven from the CLI, a UI button and huey.

**User-facing:** yes — Settings → GitHub connections, Settings → Repositories (discovery), and the Sync page.

---

## Context

### What exists

- **Tables only.** Phase 2 shipped every model this phase writes to, with no behaviour attached:
  `connections.GitHubConnection` (`token_encrypted` BinaryField, `token_last4`, `token_login`, `expires_at`,
  `status` ∈ `unverified/ok/degraded/invalid/expired`, `last_check_result` JSON, `rate_limit_remaining`,
  `rate_limit_reset_at`, reserved `app_id`/`installation_id`/`private_key_encrypted`);
  `catalog.{Organization,Repository,Project,Person,Identity,AppSetting}` (`Repository.connection` is
  `PROTECT`, plus `sync_since`, `last_synced_at`, `sync_cursor`); `activity.{PullRequest,Commit,
  PullRequestCommit,PRFile,Review,ReviewComment,CheckStatus}`; `github_sync.SyncRun`
  (`stats`, `stats_by_connection`, `error_log`, `started_at = default=timezone.now` — phase 2 changed it away
  from `auto_now_add` *for this phase*).
- **Secret masking is already installed.** `config/security.py::mask_secrets` (prefixes `ghp_ gho_ ghu_ ghs_
  ghr_ github_pat_`, keeps last 4) and `config/logging_filters.py::SecretMaskingFilter` on the root logger
  since phase 1. `tests/test_logging.py` exercises them (untracked in git; not this phase's business).
- **Settings.** `config/settings/base.py` already reads `FIELD_ENCRYPTION_KEYS` (list, empty default),
  `STORE_RAW_PAYLOADS`, `REPORT_TIMEZONE`, `DATA_DIR`; logs go to `DATA_DIR/logs/pr-radar.log`.
  `SETTING_DEFS` (`apps/catalog/setting_defs.py`) already holds `BACKFILL_DAYS=180`,
  `DEFAULT_CONNECTION_KIND="fine_grained_pat"`, `CONNECTION_CHECK_INTERVAL_HOURS=24`, `EXCLUDED_PATH_GLOBS`,
  `TEST_PATH_GLOBS`. It holds **no** sync-loop tuning keys — this phase adds them (T1).
- **Gates that bind.** `conftest.py` wraps every test in `respx.mock(assert_all_mocked=True)` (so an unmocked
  outbound request fails the suite) and forces `HUEY.immediate`. `tests/test_urls_login.py` walks
  `urlpatterns` and requires each new named URL to be reversible with `SAMPLE_ARGS` and to redirect anonymous
  users. `tests/test_translations.py` fails on an empty/fuzzy `msgstr` or a stale `.mo`.
  `tests/test_css_tokens.py::test_app_css_is_not_stale` hashes every `.html`/`.py`/`.js` under `templates/`,
  `apps/`, `static/js/` **excluding `apps/*/migrations` and `apps/*/tests`** — so `make css` must be re-run.
  `tests/test_no_hardcoded_colors.py` forbids colour literals in `apps/**` and templates.
  `mypy` covers `apps/*/services.py` and `apps/*/selectors.py` only.
- **UI shell.** `templates/base.html` + `partials/nav.html` (nav has Overview/theme/language/logout only),
  `apps/dashboards` owns `/`. htmx and Alpine are loaded; `hx-headers` carries CSRF.
  `apps/accounts/services.py::record_audit()` and the `admin` group's `catalog.manage_settings` permission
  exist and are unused so far.
- **`scope_for_user()`** returns an unrestricted `ScopeFilter`; phase 8 narrows it.

### What this phase changes

Everything between a token in an admin's hands and rows in `activity`. Two apps grow real behaviour:
`connections` (encryption, the `GitHubAuth` protocol, verification, the connection + discovery UI, two
management commands) and `github_sync` (the httpx client, rate budgeting, retries, mappers, upserts, the sync
orchestrator, `SyncRun` writing, the huey task, `manage.py sync`, the Sync page). `catalog` gains sync-tuning
`AppSetting` rows and repository create/rebind services. Nothing in `activity`, `ai_detection`, `policy` or
`metrics` changes — `process_pull_request()` lands as the named, tested, currently empty hook those phases fill.

### Key files

```
config/settings/base.py                       # + GITHUB_API_BASE_URL, GITHUB_GRAPHQL_URL
apps/catalog/setting_defs.py                  # + 8 sync/connection keys
apps/catalog/migrations/0003_sync_settings.py # NEW  seed the new keys (inline loop, like 0002)
apps/catalog/services.py                      # + create_repositories_from_discovery(), rebind_repository()

apps/connections/crypto.py                    # NEW  MultiFernet encrypt/decrypt, model-free
apps/connections/auth.py                      # NEW  GitHubAuth protocol + PATAuth + auth_for_connection()
apps/connections/services.py                  # NEW  set_token/plaintext_token/verify_connection/alerts
apps/connections/check_codes.py               # NEW  code -> lazy message template, rendered at read time
apps/connections/forms.py views.py urls.py    # NEW  connection CRUD + verify (htmx) + discovery
apps/connections/context_processors.py        # NEW  connection_alerts (admins only)
apps/connections/templates/connections/*.html # NEW  list/form/check fragment/discovery/banner
apps/connections/management/commands/{bootstrap_connection,rotate_encryption_key}.py   # NEW

apps/github_sync/errors.py                    # NEW  GitHubSchemaError & friends + require()
apps/github_sync/rate_limit.py                # NEW  per-connection RateBudget + retry policy
apps/github_sync/queries.py                   # NEW  GraphQL documents
apps/github_sync/client.py                    # NEW  httpx client, pagination, retries, budget
apps/github_sync/mappers.py                   # NEW  payload -> model field dicts (pure)
apps/github_sync/upserts.py                   # NEW  update_or_create per entity, one txn per PR
apps/github_sync/pipeline.py                  # NEW  process_pull_request() hook
apps/github_sync/services.py                  # NEW  run_sync(), lock, stats, error_log
apps/github_sync/tasks.py                     # NEW  huey task
apps/github_sync/models.py                    # + SyncLock  (migration 0002_synclock)
apps/github_sync/management/commands/sync.py  # NEW
apps/github_sync/{views,urls}.py + templates  # NEW  Sync page + htmx polling fragment

tests/fixtures/github/*.json                  # NEW  the fixture corpus
tests/test_token_leak.py                      # NEW  the §12 leak test
docs/GITHUB_CONNECTIONS.md  docs/user/connect-github.md  CHANGELOG.md
e2e/plans/connections.plan.yaml  e2e/web/test_connections.py
```

---

## Design

### 1. Credentials at rest (`connections.crypto`, ADR 0004)

`crypto.py` is model-free so migrations and commands can import it:

```python
def get_fernet() -> MultiFernet          # from settings.FIELD_ENCRYPTION_KEYS; raises ImproperlyConfigured if empty
def encrypt_token(plaintext: str) -> bytes
def decrypt_token(ciphertext: bytes) -> str   # tries every key (MultiFernet)
def token_last4(plaintext: str) -> str
```

`connections/services.py` holds the **only** two callers:

```python
def set_token(connection, plaintext, *, actor=None) -> None   # writes token_encrypted + token_last4, audits (no token)
def plaintext_token(connection) -> str                        # THE single decrypt accessor
```

Nothing else in the codebase may call `decrypt_token`; `plaintext_token()` returns to a local variable, is
never assigned to a model field, interpolated into a URL or put in an exception. A test greps `apps/` for
`decrypt_token(` outside `connections/{crypto,services}.py` and their tests.

`manage.py rotate_encryption_key` decrypts every non-null `token_encrypted` (and `private_key_encrypted`, which
is always null in v1) with the full key list and re-encrypts with key #0, inside one transaction, printing
counts only. `--dry-run` reports how many rows would move. Refuses to run with fewer than one key.

**Test keys.** `config/settings/local.py` is what pytest uses; `FIELD_ENCRYPTION_KEYS` defaults to `[]`. A
session-scoped `conftest.py` fixture sets two deterministic Fernet keys so encryption tests do not depend on the
developer's `.env`, and one test asserts the empty-key case raises `ImproperlyConfigured` rather than storing
plaintext.

### 2. The auth abstraction (`connections.auth`)

```python
class GitHubAuth(Protocol):
    def get_headers(self) -> dict[str, str]: ...
    def get_git_credentials(self) -> tuple[str, str]: ...
    @property
    def rate_limit_key(self) -> str: ...


@dataclass(frozen=True)
class PATAuth:  # serves fine_grained_pat and classic_pat
    connection_id: int
    _token: str  # never logged; __repr__ is overridden to hide it
```

`auth_for_connection(connection) -> GitHubAuth` is the factory and the only place `plaintext_token()` is
called for sync. `rate_limit_key` is `f"connection:{connection_id}"`. `get_git_credentials()` returns
`("x-access-token", token)` for phase 9's `GIT_ASKPASS`. `PATAuth.__repr__`/`__str__` return
`PATAuth(connection_id=N)` — a token must not reach a traceback's local-variable rendering by accident.

### 3. Client, errors, shape validation (`github_sync.client`, `errors`)

Endpoints come from settings (`GITHUB_API_BASE_URL="https://api.github.com"`, `GITHUB_GRAPHQL_URL=
f"{base}/graphql"`) so respx routes are exact and no test can wander to a real host.

```python
class GitHubError(Exception)                      # base
class GitHubSchemaError(GitHubError)              # .path, e.g. "data.repository.pullRequests.nodes[0].author.login"
class GitHubAuthError(GitHubError)                # 401
class GitHubSSOError(GitHubError)                 # 403 with X-GitHub-SSO
class GitHubServerError(GitHubError)              # 5xx after retries
class SecondaryRateLimitError(GitHubError)        # 403/429 with a retry hint
class RateBudgetExhausted(GitHubError)            # primary budget, carries reset_at
```

`errors.require(payload, path: str)` walks a dotted/indexed path and raises `GitHubSchemaError(path)` with the
**full path of the first missing segment** — never the payload itself, so a body carrying a token-shaped string
cannot end up in a message. Optional fields use `errors.optional(payload, path, default=None)` which returns
`None`, never `0` (spec §15).

`GitHubClient(auth, budget, *, client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep)`:

- `graphql(document, variables)` — POSTs, applies the budget, retries, then returns `data`. Every document
  includes `rateLimit { remaining resetAt cost }`; the client feeds it to the budget before returning.
  A `errors` array in the body maps to `GitHubAuthError` (type `FORBIDDEN`/`UNAUTHORIZED` on the whole
  document) or `GitHubError`.
- `rest_get(path)` — for `/user` and header-only checks (`X-OAuth-Scopes`, `X-GitHub-SSO`,
  `github-authentication-token-expiration`), which GraphQL cannot give.
- `paginate(document, variables, *, page_path, page_size)` — a generator over `nodes`, following
  `pageInfo.hasNextPage`/`endCursor`. Used for both top-level PR pages and **nested** connections (reviews,
  review threads, commits, files): when a nested `pageInfo.hasNextPage` is true the client issues a follow-up
  query for that PR's connection and concatenates. A nested page that cannot be completed raises — it is never
  silently truncated (ADR 0003).
- `sleep` is injected so rate-limit and backoff tests assert the *durations requested* instead of waiting.

**Retries.** 502/503/504, `SecondaryRateLimitError` and `httpx.TransportError`: 5 attempts, exponential
backoff `min(2**n, 60)` seconds with ±20 % jitter, honouring `Retry-After` verbatim when the header is present
(seconds form; an HTTP-date form is parsed to a delta). 401/403 are never retried.

**Primary budget** (`rate_limit.RateBudget`, one instance per `rate_limit_key`, held in a dict on the sync run
for the process lifetime): updated from every `rateLimit` block; `remaining < RATE_LIMIT_MIN_REMAINING` (200)
sleeps until `resetAt` and records a `rate_limit_wait` entry on `SyncRun.stats_by_connection`. Because the
budget is per connection, the orchestrator groups repositories by connection and a waiting connection yields to
the others rather than blocking the run.

### 4. Verification (`connections.services.verify_connection`)

Runs the spec §5.1 checks and stores **codes plus parameters only**:

```json
{"checked_at": "...", "checks": [
  {"code": "TOKEN_USER_OK",      "outcome": "ok",          "params": {"login": "octocat"}},
  {"code": "REPOS_VISIBLE",      "outcome": "ok",          "params": {"count": 12, "sample": ["a/b","a/c"]}},
  {"code": "PERM_PULL_REQUESTS", "outcome": "ok",          "params": {}},
  {"code": "PERM_CONTENTS",      "outcome": "unavailable", "params": {}},
  {"code": "SSO_AUTHORIZATION_REQUIRED", "outcome": "fail","params": {"org": "acme", "url": "https://…"}},
  {"code": "CLASSIC_PAT_WRITE_SCOPE",    "outcome": "fail","params": {"scopes": ["repo"]}},
  {"code": "RATE_LIMIT",         "outcome": "ok",          "params": {"remaining": 4980, "reset_at": "…"}}
]}
```

`check_codes.py` maps each code to a lazy message **and** a lazy "what to do" hint with named placeholders;
templates render them in the reader's language (CLAUDE.md: never store a rendered English message). A test
asserts every code emitted by `verify_connection` has an entry and that no stored `params` value contains a
token shape. Outcome → status: any `fail` on an auth check ⇒ `invalid`; SSO/permission gaps ⇒ `degraded`;
`expires_at` in the past ⇒ `expired`; otherwise `ok`. Verification is throttled to once per hour per connection
(`CONNECTION_RECHECK_MIN_MINUTES`) — `verify_connection(force=True)` bypasses it for the explicit UI button.

**Banner.** `connections.context_processors.connection_alerts` returns the list of connections that are
`invalid`/`expired` or expire within `TOKEN_EXPIRY_WARNING_DAYS` (14), **only** for users holding
`catalog.manage_settings`, in one query, with a test asserting the query count and that a lead sees nothing.

### 5. Bootstrap (`manage.py bootstrap_connection`)

If `GITHUB_TOKEN` is in the environment and `GitHubConnection.objects.exists()` is false, creates
`"Default (.env)"` with `kind` from `DEFAULT_CONNECTION_KIND`, encrypts the token and leaves status
`unverified` (it does **not** call GitHub — intake forbids it; `--verify` is an explicit opt-in flag that the
tests exercise only against respx). If connections already exist it logs a warning naming the `.env` variable
and exits 0. Unit-tested with `monkeypatch.setenv`; never executed against the real API.

### 6. Discovery (`connections.views.discover` + `catalog.services`)

`GET /settings/repositories/discover/?connection=<pk>` lists repositories reachable through one connection via
GraphQL (`repositoryOwner(login:$owner).repositories` when `owner_login` is set, else `viewer.repositories`),
grouped by owner, archived ones hidden behind a checkbox, each row showing whether it is already bound to a
different connection. Results are **not** persisted. `POST` creates `Organization` + `Repository` rows
(`update_or_create` on `github_id`) with `sync_since` (default `today - BACKFILL_DAYS`) and optional project
assignment. `POST /settings/repositories/<pk>/rebind/` moves a repository to another connection after an
explicit confirm step, writing an `AuditEntry`; historical data is untouched. Deleting a connection with
repositories is already refused by `PROTECT` — the UI turns the `ProtectedError` into a visible message
listing the repositories to rebind or deactivate first.

### 7. The sync algorithm (§5.3, `github_sync.services.run_sync`)

```
run_sync(trigger, repo_full_names=None, project_slug=None, since=None, full=False, actor=None) -> SyncRun
  acquire SyncLock                       # else raise SyncAlreadyRunning
  SyncRun(status=running, trigger=…)
  repositories = active repos, filtered, grouped by connection
  for connection, repos in groups:
      if connection inactive/invalid: record skip; continue
      auth = auth_for_connection(connection); client = GitHubClient(auth, budget_for(connection))
      for repo in repos:
          try: sync_repository(client, repo, run, stats)
          except GitHubAuthError:   connection.status = invalid; skip all remaining repos of this connection
          except GitHubSSOError:    connection.status = degraded; skip remaining repos of this connection
          except GitHubError as e:  record in error_log; continue with the next repository
  status = success | partial (any error) | failed (no repository completed)
```

`sync_repository`:

1. `since = sync_since` if `full` else `max(last_synced_at - SYNC_OVERLAP_MINUTES, sync_since)`.
2. Page `repository.pullRequests(orderBy:{field:UPDATED_AT,direction:DESC}, first:SYNC_PR_PAGE_SIZE)`; stop at
   the first node with `updatedAt < since`.
3. For each PR: fetch/complete nested connections (timeline items, reviews, review threads, commits with
   trailers + `statusCheckRollup`, files), then `upserts.upsert_pull_request()` inside
   `transaction.atomic()` — one transaction per PR.
4. `transaction.on_commit(lambda: process_pull_request(pr_id))`.
5. A failing PR is appended to `error_log` (masked) and the repository continues.
6. `last_synced_at = run.started_at` **only** after the repository's last page succeeds.

`upserts.py` writes with `update_or_create` keyed on `github_id` (PR, Review, ReviewComment, Commit,
Organization, Repository) or `(repository, sha)` for commits without a node id, `(pull_request, path)` for
`PRFile`, `(pull_request, commit_sha)` for `CheckStatus`, `(pull_request, commit)` for `PullRequestCommit`.
`Identity` rows are `get_or_create((kind, value))` with `person=None`; mapping identities to people is phase 4.
`raw` is populated only when `STORE_RAW_PAYLOADS` is on. `mappers.py` is pure: payload → field dict, raising
`GitHubSchemaError` for anything the writer depends on, returning `None` for anything optional.

`pipeline.process_pull_request(pull_request_id)` is defined, imported by the orchestrator, logged at debug and
**does nothing** in this phase. Its test asserts it is called exactly once per synced PR (via
`django_capture_on_commit_callbacks`) so phases 4–6 plug in without touching the orchestrator.

**The lock.** `github_sync.SyncLock` (`name` unique, `acquired_at`, `sync_run` FK) — acquisition is
`objects.create(name="global")` and an `IntegrityError` means another run holds it; release is a delete in a
`finally`. A lock older than `SYNC_LOCK_STALE_MINUTES` is stolen (a killed process must not wedge the tool
forever). Chosen over `select_for_update`, which SQLite does not honour.

**`SyncRun` bookkeeping.** `stats = {"repositories": n, "pull_requests": n, "commits": n, "reviews": n,
"review_comments": n, "files": n, "check_statuses": n, "errors": n}`;
`stats_by_connection = {"<id>": {"name": …, "repositories": n, "skipped": n, "errors": n,
"rate_limit_remaining": …, "rate_limit_reset_at": …, "rate_limit_waits": n, "status": …}}`.
Every `error_log` append goes through `config.security.mask_secrets` — one function, one code path.

### 8. Surfaces, URLs and i18n

| URL | View | Who | htmx |
|---|---|---|---|
| `/settings/connections/` | list + status + last check table | `catalog.manage_settings` | — |
| `/settings/connections/new/`, `/<pk>/edit/` | form (token write-only) | admin | — |
| `/settings/connections/<pk>/check/` (POST) | re-verify | admin | fragment |
| `/settings/connections/<pk>/deactivate/` (POST) | toggle | admin | fragment |
| `/settings/repositories/discover/` | discovery list + add | admin | fragment |
| `/settings/repositories/<pk>/rebind/` (POST) | re-bind with confirm | admin | fragment |
| `/sync/` | SyncRun list + "Sync now" | authenticated (lead+) | — |
| `/sync/run/` (POST) | enqueue the huey task | lead+ | fragment |
| `/sync/status/` | running-run progress | lead+ | polled fragment |

Every one of these returns a **fragment** for `HX-Request` and a full page otherwise; errors render a visible
fragment, never an empty 400. Mutations are POST. Nav gains a "Sync" link and a "Settings" group. All new
strings are translated into Ukrainian in this phase (T22) — full sentences, named placeholders.

### Architecture conformance and deviations

Honoured as written: ADR 0003 (poll-only, per-repository watermark + overlap, one transaction per PR,
per-connection budget, 401 quarantines a connection, mandatory nested pagination, one run at a time), ADR 0004
(MultiFernet at rest, one accessor, `GitHubAuth` protocol, no token in admin/UI/logs/`error_log`), ADR 0005
(every huey task is also a management command), ADR 0006 (fragments chosen by `HX-Request`).

Deviations, each appended to `.autodev/DECISIONS.md`:

1. **`SyncLock` is a new model with unique-row acquisition and a stale-lock steal.** ADR 0003 says "a lock row
   in the database" without naming the mechanism; `select_for_update` is a no-op on SQLite, so the uniqueness
   constraint is the portable equivalent, and a stale timeout is needed because a killed worker leaves the row.
2. **Eight new `AppSetting` keys + `catalog/0003_sync_settings.py`.** Spec §5.3/§5.4 name the numbers
   (`SYNC_OVERLAP`, `remaining < 200`, page size 50) but phase 2's registry has none of them.
3. **`GITHUB_API_BASE_URL` / `GITHUB_GRAPHQL_URL` as Django settings, not `AppSetting` rows** — they are
   deployment wiring (and the thing respx pins), not operator-tunable policy; GHES support later changes one
   env var.
4. **Connections, repositories and sync runs are read through `services.py`, not `selectors.py`** — following
   the p02 precedent for global, unscoped settings data, so CLAUDE.md's "every selector starts from
   `scope_for_user`" stays absolute instead of gaining an exception. Phase 8 adds scoped selectors where PR data
   is read.
5. **`process_pull_request()` ships empty.** The hook, its call site and its once-per-PR test are this phase's
   deliverable; the four pipeline stages are phases 4–6.
6. **Note, not a change:** ADR 0003 mentions `AISignal` unique on `(pull_request, rule, evidence_hash)`; phase 2
   deliberately shipped no such constraint (`## p02-plan`). Nothing in this phase depends on it.

---

## Tasks

- [x] **T1: sync settings + endpoints.** Add `GITHUB_API_BASE_URL`/`GITHUB_GRAPHQL_URL` to
  `config/settings/base.py`; add to `SETTING_DEFS` (group `sync`): `SYNC_OVERLAP_MINUTES=60`,
  `SYNC_PR_PAGE_SIZE=50`, `SYNC_NESTED_PAGE_SIZE=100`, `RATE_LIMIT_MIN_REMAINING=200`, `SYNC_MAX_RETRIES=5`,
  `SYNC_RETRY_MAX_SECONDS=60`, `SYNC_LOCK_STALE_MINUTES=360`, `TOKEN_EXPIRY_WARNING_DAYS=14`,
  `CONNECTION_RECHECK_MIN_MINUTES=60`; data migration `catalog/0003_sync_settings.py` (inline idempotent loop,
  reverse = `noop`, like `0002`). Tests: the existing `SETTING_DEFS`-parametrized seeding test covers the new
  keys; add `apps/catalog/tests/test_app_settings.py::test_sync_settings_migration_is_idempotent`.
- [x] **T2: token encryption + rotation.** `apps/connections/crypto.py`,
  `services.set_token/plaintext_token`, `management/commands/rotate_encryption_key.py`, conftest key fixture.
  Tests (`apps/connections/tests/test_crypto.py`): round-trip; ciphertext ≠ plaintext and contains no
  plaintext fragment; a token encrypted with key B decrypts after B is appended second (rotation order);
  empty `FIELD_ENCRYPTION_KEYS` raises `ImproperlyConfigured`; `token_last4` is stored and the model has no
  plaintext column; `rotate_encryption_key` re-encrypts every row under key #0 and is a no-op the second time;
  `--dry-run` writes nothing; a grep test asserts `decrypt_token(` appears only in `crypto.py`,
  `services.py` and their tests.
- [x] **T3: the `GitHubAuth` protocol.** `apps/connections/auth.py` (`GitHubAuth`, `PATAuth`,
  `auth_for_connection`). Tests (`test_auth.py`): headers carry `Authorization: Bearer …` and the documented
  GraphQL `Accept`; `rate_limit_key` differs per connection and is stable; `get_git_credentials()` returns
  `("x-access-token", token)`; `repr()`/`str()`/`pytest`-rendered locals contain no token fragment beyond
  nothing at all; an inactive or token-less connection raises a named error.
- [x] **T4: errors and shape validation.** `apps/github_sync/errors.py` with the exception tree,
  `require()` and `optional()`. Tests (`apps/github_sync/tests/test_errors.py`): a missing leaf, a missing
  intermediate node and a short list index each raise `GitHubSchemaError` whose `str()` names the **full**
  dotted path; `optional()` returns `None` (never `0`) for a missing numeric field; no error message contains
  the payload.
- [x] **T5: fixture corpus + loader.** `tests/fixtures/github/`: `rest_user.json`, `rate_limit.json`,
  `discovery_repos_page1.json`/`page2.json`, `pull_requests_page1.json` (2 PRs)/`page2.json`,
  `pr_reviews_page2.json`, `pr_commits_page2.json`, `pr_files_page2.json` (each nested set > 100 items across
  two pages), `pr_missing_author_login.json`, `graphql_errors_unauthorized.json`,
  `secondary_rate_limit.json`, `server_error_502.json`. Add a `github_fixture(name)` fixture to `conftest.py`
  and a `respx` helper that binds a document name to a response. Test: every fixture parses and matches the
  GraphQL envelope shape (`data` + `rateLimit`), so a malformed fixture fails loudly rather than mid-client.
- [x] **T6: rate budget + retry policy.** `apps/github_sync/rate_limit.py` (`RateBudget`, `backoff_delays`,
  `retry_after_seconds`). Tests (`test_rate_limit.py`): a `rateLimit` block below 200 requests a sleep until
  `resetAt` (freezegun + injected sleeper) and logs it; two budgets with different keys are independent —
  exhausting one leaves the other ready; `Retry-After: 30` yields exactly 30 s, an HTTP-date form yields the
  delta, absence yields the jittered exponential; the delay never exceeds `SYNC_RETRY_MAX_SECONDS`;
  attempts stop at `SYNC_MAX_RETRIES` and then raise.
- [x] **T7: the client.** `apps/github_sync/queries.py` + `client.py`. Tests (`test_client.py`, respx):
  a GraphQL call sends the auth header and the rate-limit block and returns `data`; `paginate` follows
  `pageInfo` across two pages; a **nested** connection of 150 reviews is fully paginated (all 150 returned, a
  second request issued); a nested page that errors raises rather than returning a short list; 502 then 200
  succeeds after one retry; a secondary rate limit honours `Retry-After` then succeeds; 401 raises
  `GitHubAuthError` without retrying; 403 + `X-GitHub-SSO` raises `GitHubSSOError` carrying the org and URL;
  `pr_missing_author_login.json` raises `GitHubSchemaError` naming the JSON path.
- [x] **T8: connection verification.** `connections/check_codes.py` + `services.verify_connection()`.
  Tests (`test_verify.py`): a healthy fixture set stores `ok` with the documented codes and sets `token_login`
  and `expires_at` from the header; a 401 fixture stores `invalid` plus `AUTH_FAILED`; a classic PAT with a
  `repo` scope stores `degraded` + `CLASSIC_PAT_WRITE_SCOPE`; a 403 + SSO stores `degraded` +
  `SSO_AUTHORIZATION_REQUIRED` with the org and URL in `params`; an expired `expires_at` yields `expired`;
  no stored `params` value matches a token shape; every emitted code has a `check_codes` entry; a second call
  within the hour is throttled and `force=True` is not.
- [x] **T9: connections UI.** `forms.py` (token write-only, "save as unverified" opt-in), `views.py`,
  `urls.py` mounted at `/settings/connections/`, four templates, nav entry, `record_audit` on create/replace/
  deactivate. Tests (`test_views.py`): a lead gets 403 and an admin 200 on every URL (positive + refusal on the
  same surface); the create form verifies before saving and refuses to save an unverified connection unless the
  opt-in box is ticked; the rendered list and detail HTML contain `token_last4` and **no** token fragment and no
  reveal control; the check button returns a fragment for `HX-Request` and a full page otherwise; an invalid
  token renders a visible error fragment, not an empty 400; `AuditEntry` rows are written and contain no token.
- [x] **T10: expiry / invalid banner.** `connections/context_processors.py` + a partial included from
  `base.html`. Tests: an admin sees the banner for an `invalid`, an `expired` and a 13-days-to-expiry
  connection, and not for a 30-day one; a lead never sees it; the processor costs one query
  (`assertNumQueries`) and zero when the user lacks the permission.
- [x] **T11: `bootstrap_connection`.** Command + tests: with `GITHUB_TOKEN` set and no connections, creates
  "Default (.env)" with the right kind and a decryptable token; with a connection already present, creates
  nothing and logs the warning naming the variable; with no variable, exits 0 saying so; the command's stdout
  and the log record contain no token beyond last4; `--verify` calls the client only against respx.
- [x] **T12: discovery + rebinding.** `catalog/services.py` additions + the discovery view, templates and the
  rebind confirm flow. Tests: the discovery list groups by owner, hides archived by default, and marks a repo
  already bound elsewhere; submitting a selection creates `Organization`/`Repository` with `sync_since =
  today - BACKFILL_DAYS` and the chosen project; submitting the same selection twice creates no second row;
  rebinding moves `Repository.connection`, keeps every PR, and writes an `AuditEntry`; deleting a connection
  with repositories renders the visible "rebind these first" message listing them, and the connection survives.
- [x] **T13: mappers.** `apps/github_sync/mappers.py` for PR, commit, review, review comment, file, check
  status, timeline. Tests (`test_mappers.py`): each mapper produces the documented field dict from the fixture;
  a missing depended-on field raises `GitHubSchemaError` with the path; a missing optional field maps to `None`,
  not `0`; `raw` is populated only when `STORE_RAW_PAYLOADS` is on; a PR with no author (ghost user) maps to a
  null author rather than failing.
- [x] **T14: upserts.** `apps/github_sync/upserts.py`. Tests (`test_upserts.py`): a full fixture PR creates the
  expected `PullRequest`, `Commit`, `PullRequestCommit`, `Review`, `ReviewComment`, `PRFile` and `CheckStatus`
  rows with the expected field values; running the same upsert twice changes no row count and no `github_id`
  or primary key; a changed title on the second pass updates in place; the whole PR is written in one
  transaction (a mapper failure on the last child leaves **no** partial rows).
- [x] **T15: the post-processing hook.** `apps/github_sync/pipeline.py::process_pull_request`. Tests: it is
  called exactly once per synced PR, after commit (`django_capture_on_commit_callbacks`), with the PR's id, and
  a raising hook does not roll back the PR's rows but is recorded in `error_log`.
- [x] **T16: the orchestrator.** `github_sync.models.SyncLock` + `0002_synclock`, `services.run_sync`,
  `sync_repository`, stats and masked `error_log`. Tests (`test_sync.py`): a full fixture sync sets
  `SyncRun.status=success` with the documented `stats`; a second run over the same fixtures creates zero rows
  (the idempotency criterion); `last_synced_at` advances only after the repository finishes, and stays put when
  its last page raises; a PR error is recorded and the repository continues; a repository error is recorded and
  the run continues with `status=partial`; the watermark uses `last_synced_at - SYNC_OVERLAP_MINUTES` and never
  goes earlier than `sync_since`; a second concurrent `run_sync` raises `SyncAlreadyRunning`; a lock older than
  `SYNC_LOCK_STALE_MINUTES` is stolen; the lock is released after a crashing run.
- [x] **T17: credential failures quarantine one connection.** Tests (`test_sync_isolation.py`): with two
  connections, a 401 on connection A marks it `invalid`, skips **only** A's repositories for the rest of the
  run, and B's repositories still sync with rows written; the same for 403 + SSO ⇒ `degraded`; a connection
  whose budget is exhausted yields to the other connection instead of stalling the run;
  `stats_by_connection` records the skip count and the rate-limit state per connection.
- [x] **T18: `manage.py sync`.** `--repo owner/name` (repeatable), `--project slug`, `--since YYYY-MM-DD`,
  `--full`. Tests: each filter selects the expected repository set; `--since` overrides the watermark;
  `--full` starts from `sync_since`; a second invocation while a run holds the lock exits non-zero with a clear
  message; output contains no token; unknown `--repo` fails with a named error, not a traceback.
- [x] **T19: huey task + Sync page.** `tasks.py` (`sync_task`, a thin wrapper over `run_sync`), `views.py`,
  `urls.py`, `templates/github_sync/{sync.html,partials/status.html,partials/runs.html}`, nav entry. Tests:
  POST `/sync/run/` enqueues (HUEY immediate) and returns a fragment; the polled status endpoint reports the
  running run's counters and stops polling when it finishes; the run list shows per-connection stats and a
  masked error log; GET returns a full page for a normal request and a fragment for `HX-Request`; anonymous is
  redirected (covered by the existing URL walk); `assertNumQueries` on the run list so it cannot become an N+1.
- [x] **T20: the leak test.** `tests/test_token_leak.py`: run a sync that fails partway with a token-shaped
  secret, then assert the literal token (and every fragment longer than its last 4 characters) appears in
  **none** of: any value of any column of any table (walked via `connection.introspection`), the log file at
  `DATA_DIR/logs/pr-radar.log`, `SyncRun.error_log`, and the rendered HTML of the connections list, connection
  detail and sync pages. Also assert a plaintext grep of the on-disk SQLite file finds nothing.
- [x] **T21: docs.** `docs/GITHUB_CONNECTIONS.md` (token types and required scopes, the first-run checklist,
  verification codes and what to do about each, rotation, recovery when `FIELD_ENCRYPTION_KEYS` is lost,
  rebinding, the `.env` bootstrap path), `docs/user/connect-github.md` (task-shaped: connect, discover, sync),
  a `CHANGELOG.md` entry, and `docs/SETUP.md`/`CONFIGURATION.md` cross-links for the new settings. Test: a doc
  test asserts `docs/GITHUB_CONNECTIONS.md` mentions every `check_codes` code, so a new code cannot ship
  undocumented.
- [x] **T22: Ukrainian parity + generated artefacts.** `make messages` (translate every new msgid, plurals via
  `ngettext`, named placeholders) and `make css`; commit `locale/**`, `static/css/app.css` and
  `.build-manifest.sha256`. The existing `tests/test_translations.py` and `test_css_tokens.py` are the gate;
  add a render test that the connections and sync pages in `uk` contain none of the canary English strings.
- [x] **T23: e2e.** `e2e/plans/connections.plan.yaml` + `e2e/web/test_connections.py`, with `seed_e2e`
  extended to create one `ok` connection, one expiring one, a repository and a finished `SyncRun`. Cases: an
  admin opens Settings → Connections and sees the connection with `…last4` and no token; the expiry banner is
  visible to the admin and absent for the lead; the Sync page lists the seeded run. **No** e2e case triggers a
  sync — the live surface has no credentials and must make no outbound call.
- [x] **T24: gate.** Run the full verification block below; fix findings; update `docs/PROGRESS.md` and
  `.autodev/DECISIONS.md` with anything the implementation had to decide.

---

## Verification

```bash
# the project gate (CLAUDE.md "lint" + "test")
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py check
uv run pytest -q

# migrations from zero, including the new AppSetting seed and SyncLock
rm -f /tmp/pr-radar-zero.sqlite3
DATABASE_URL=sqlite:////tmp/pr-radar-zero.sqlite3 uv run python manage.py migrate --noinput
rm -f /tmp/pr-radar-zero.sqlite3

# the two new commands are non-interactive and make no live call
uv run python manage.py bootstrap_connection            # no GITHUB_TOKEN set -> exits 0 with a message
uv run python manage.py rotate_encryption_key --dry-run

# generated artefacts this phase touches (both diffs must be empty after committing)
make css      && git diff --stat static/css/app.css static/css/.build-manifest.sha256
make messages && git diff --stat locale/

# e2e
make e2e-up && uv run pytest e2e -q; make e2e-down
```

| # | Acceptance criterion | Test that proves it |
|---|---|---|
| 1 | Syncing the fixtures creates the expected `PullRequest`, `Commit`, `Review`, `PRFile` and `CheckStatus` rows with correct fields | `apps/github_sync/tests/test_upserts.py::test_full_pull_request_is_written_with_expected_fields`; `test_sync.py::test_fixture_sync_creates_expected_rows` |
| 2 | A second sync over the same fixtures changes no row count and no `github_id` | `test_sync.py::test_second_sync_is_idempotent` (row counts and the id set snapshotted before/after); `test_upserts.py::test_upsert_twice_creates_no_rows` |
| 3 | Nested-connection pagination beyond 100 items | `test_client.py::test_nested_reviews_are_paginated_past_one_hundred` (150 reviews across two pages, both requested); `::test_truncated_nested_page_raises` |
| 4 | Secondary-rate-limit backoff honouring `Retry-After` | `test_rate_limit.py::test_retry_after_seconds_is_honoured_exactly`, `::test_retry_after_http_date_is_honoured`; `test_client.py::test_secondary_rate_limit_retries_then_succeeds` |
| 5 | A 401 on one connection marks it invalid and skips only its repositories, while another connection keeps syncing | `test_sync_isolation.py::test_401_quarantines_only_its_own_connection` |
| 6 | After a sync that fails partway, no token (beyond last4) is in the DB, the log file, `SyncRun.error_log` or any rendered page | `tests/test_token_leak.py::test_no_token_after_failing_sync` (introspection walk + log file + `error_log` + three rendered pages + raw SQLite file grep) |
| 7 | Any unmocked outbound HTTP request fails the suite | existing `conftest.py::_no_live_http_requests` (`assert_all_mocked=True`) + `tests/test_http_guard.py`; extended with `apps/github_sync/tests/test_client.py::test_unrouted_host_is_refused` |
| 8 | A fixture missing a field the client depends on raises `GitHubSchemaError` naming the JSON path | `test_errors.py::test_missing_leaf_names_the_full_path` (+ intermediate + list index); `test_client.py::test_missing_end_cursor_on_a_further_page_raises_schema_error`; `test_mappers.py::test_missing_required_field_raises_with_path` |
| — | Deliverable: `rotate_encryption_key` | `apps/connections/tests/test_crypto.py::test_rotate_reencrypts_under_first_key`, `::test_rotate_is_idempotent`, `::test_dry_run_writes_nothing` |
| — | Deliverable: `last_check_result` as codes + params, banner | `test_verify.py` (all cases), `test_context_processors.py` |
| — | Deliverable: `bootstrap_connection`, never run live | `apps/connections/tests/test_bootstrap.py` |
| — | Deliverable: `GitHubAuth` protocol | `apps/connections/tests/test_auth.py` |
| — | Deliverable: discovery + rebinding | `apps/connections/tests/test_discovery.py` |
| — | Deliverable: `process_pull_request` hook | `apps/github_sync/tests/test_pipeline.py::test_hook_runs_once_per_pr_on_commit` |
| — | Deliverable: `manage.py sync`, huey task, Sync page with polling | `apps/github_sync/tests/test_command_sync.py`, `test_views.py` |
| — | Deliverable: `docs/GITHUB_CONNECTIONS.md` | `tests/test_docs.py::test_every_check_code_is_documented` |
| — | Convention: Ukrainian parity in the same phase | existing `tests/test_translations.py` + `apps/connections/tests/test_views.py::test_uk_render_has_no_canary_english` |
| — | Convention: fragment vs full page by `HX-Request`, visible error fragments | `test_views.py::test_hx_request_returns_fragment`, `::test_invalid_token_renders_visible_error` |
| — | Convention: no N+1 on a list view | `test_views.py::test_sync_run_list_query_count`, `test_context_processors.py::test_processor_costs_a_fixed_low_query_count_for_admin_and_zero_for_lead` |

### Case selection (per `.autodev/guides/case-taxonomy.md`)

**Happy path:** verify → discover → sync → rows exist. **Input and boundaries:** an empty key list, a token
with no `gh` prefix, a PR with a ghost author, a nested connection of exactly 100 and of 150, `Retry-After: 0`,
`sync_since` later than the watermark. **State:** no connections (empty state on both settings pages), one
connection, two connections, a run in progress, the state after a failed run. **Errors:** 401, 403+SSO, 502,
secondary rate limit, schema drift, `ProtectedError` on delete — each asserted by the message a user sees, not
by a blank page. **Permissions and identity:** every admin surface gets a lead refusal *and* an admin success
on the same URL; the banner gets both. **Idempotency and repetition:** the second sync, the double-clicked
Sync button (lock), a re-run of `rotate_encryption_key`, a re-submitted discovery selection, re-running the
seed migration. **Asynchronous chains:** the huey task and the polled status fragment, asserted on the
observable end state rather than a sleep. **Persistence:** `last_synced_at` survives a crash; the token
survives key rotation. **Concurrency:** two `run_sync` calls (the lock) — the only place the spec makes it
possible. Deliberately **not authored:** accessibility and platform-sanity cases for these settings pages
(`deferred_not_authored` — phase 10 owns the a11y and dark-mode sweep across every page at once), a
`GITHUB_APP` auth case (not in v1), and any case requiring a live GitHub call (forbidden by intake).

---

## Risks

| Risk | What this plan does |
|---|---|
| **#2 token leak** (the phase's headline risk) | MultiFernet at rest with keys only in `.env`; exactly one decrypt accessor, enforced by a grep test; `PATAuth.__repr__` hides the token so a traceback cannot render it; `error_log` written only through `mask_secrets`; the form is write-only with no reveal; T20 is the §12 leak test over the DB, the SQLite file itself, the log file, `error_log` and three rendered pages **after a failing sync** |
| **#9 no GitHub credentials exist for this run** | The whole phase is built against `tests/fixtures/github/`, and T5 makes a malformed fixture fail loudly; the client validates every field it depends on and raises `GitHubSchemaError` with the path instead of writing a wrong row; `bootstrap_connection` and verification are implemented and unit-tested but never executed live; `docs/GITHUB_CONNECTIONS.md` gives the operator a deliberate, observable first-run checklist |
| **#14 SQLite write contention** | One transaction per PR (short, not per repository); the `SyncLock` guarantees one sync at a time; the huey queue is on its own file; the stale-lock steal means a killed worker cannot wedge the tool |
| **#1 wrong number about a named person** | Sync only creates `Identity` rows with `person=None` — it never guesses a person, so phase 4 owns mapping and the unmapped queue; `optional()` returns `None` and never `0`, so a missing field cannot become a zero in a metric |
| **#10 dashboard latency** | `assertNumQueries` on the sync-run list and the banner context processor from the start; upserts fetch by indexed natural keys |
| **#8 Ukrainian lags** | T22 translates in-phase; `last_check_result` stores codes + params only, so the connection-check texts are translatable forever rather than baked English |
| **#3 cross-project data leak** | Every new surface is admin-only (`catalog.manage_settings`) except `/sync/`, which shows no per-project data; each surface has a lead-refusal test beside its admin-success test |
| **#4 ten-hour ceiling** | The task order is client-first, UI-last: T1–T7 (crypto, protocol, client) and T13–T17 (mappers, upserts, orchestrator) are the load-bearing half; if the phase is cut short, the UI tasks (T9, T12, T19) are the ones a follow-up absorbs |

---

## Out of scope

- **Identity → `Person` mapping, bot detection, the unmapped-identity queue, `activity.derive`** → phase 4.
  This phase only creates `Identity` rows and calls the empty hook.
- **AI detection, the disclosure parser, `ai_status`** → phase 5. **Policy evaluation** → phase 6.
  **Dirty-day marking and rollups** → phase 7. All four are stages of `process_pull_request()`, which ships
  empty here by design.
- **`manage.py recompute`, `compute_churn`, `seed_demo`, `metrics_doc`, `seed_detection_rules`** → their own
  phases; only `sync`, `bootstrap_connection` and `rotate_encryption_key` land here.
- **The PR detail page, repository/person pages, tables, filters, CSV/XLSX export** → phases 8–9. The only
  pages here are the three this phase's flow needs.
- **`GitHubAuth` for GitHub Apps** — reserved by the protocol, not implemented (spec §5.1).
- **Scoped selectors and `UserProjectAccess` enforcement** → phase 8; `scope_for_user()` stays unrestricted.
- **`git` credential handling via `GIT_ASKPASS`** → phase 9 (churn). `get_git_credentials()` exists and is
  unit-tested; nothing calls it yet.
- **Webhooks, write access to GitHub, GHES-specific handling, scheduled daily verification as a cron entry**
  (the throttle and the command exist; the launchd/cron recipe is phase 10's `docs/SETUP.md` work).
