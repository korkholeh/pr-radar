# Phase 2 — Domain models, migrations and Django admin

**Goal:** every table in spec §4 exists, migrates from zero, and is visible in Django admin with the right
read-only and hidden fields.

**User-facing:** no. No new URL, no new template, no new page. The only human surface is `/admin/`.

---

## Context

### What exists

- Two apps only: `apps/accounts` (`UserPreference`, `AuditEntry`, migrations `0001_initial` + `0002_groups`
  creating the empty `admin` / `lead` groups, `ScopeFilter` + `scope_for_user()` returning unrestricted) and
  `apps/dashboards` (Overview placeholder view, formatting helpers, no models).
- `config/settings/base.py:26` — `INSTALLED_APPS` lists `apps.accounts`, `apps.dashboards`; `REPORT_TIMEZONE`
  and `STORE_RAW_PAYLOADS` are already read from `.env` (`base.py:22,24`).
- `config/urls.py` mounts `admin.site.urls` at `/admin/`; `apps/accounts/admin.py` registers the two existing
  models. `tests/test_urls_login.py:_named_urls` **skips the `admin` namespace**, so adding ~20 models cannot
  break the deny-by-default URL parametrization.
- Gate helpers already in place and binding on this phase:
  - `tests/test_no_hardcoded_colors.py` scans `apps/**/*.py` (excluding `migrations/`) for `#hex`/`rgb(`/`hsl(`
    **and** for Tailwind palette class names.
  - `tests/test_translations.py` fails on any empty or fuzzy `msgstr` in `locale/uk/LC_MESSAGES/*.po` and asserts
    the committed `.mo` matches the `.po` source.
  - `tests/test_css_tokens.py::test_app_css_is_not_stale` compares `static/css/.build-manifest.sha256` against a
    checksum over `tokens.css`, `src/input.css` and every `.html`/`.py`/`.js` under `templates/`, `apps/`,
    `static/js/` — **excluding `apps/*/migrations` and `apps/*/tests`** (p01-review_fix2). Adding
    `apps/<app>/models.py`, `admin.py`, `factories.py`, `services.py` therefore changes the manifest and
    **`make css` must be re-run and its output committed** (task T16).
  - `conftest.py` has the autouse respx guard and `lead_user` / `admin_user` fixtures (the latter is a superuser).
- Phase-1 decisions this phase must honour (`.autodev/DECISIONS.md`, `## p01-plan`):
  - "the `admin` and `lead` groups are created empty … permissions are attached in **phase 2** when
    `catalog.manage_settings` exists, and **`UserProjectAccess` is deferred to phase 2** because it needs
    `catalog.Project`". Both land here.
  - `mypy` types `apps/*/services.py` and `apps/*/selectors.py` only — every `services.py` this phase adds must be
    annotated and clean.
- `tests/test_logging.py` is present but **untracked**: the orchestrator held it back from the phase-1 commit
  because it contains a token-shaped literal. This phase does not touch it (see *Out of scope*).

### What this phase changes

Eight new Django apps with models, migrations, admin and factories, plus two additions to `accounts`
(`UserProjectAccess`, the `catalog.manage_settings` grant). No behaviour is wired into any view: sync,
detection, policy evaluation, metric computation and churn all arrive in later phases and only need the tables
to be here, indexed and constrained, so they cannot invent a schema of their own later.

### Key files

```
config/settings/base.py                 # INSTALLED_APPS += 8 apps
config/admin.py                         # NEW — ReadOnlyAdminMixin, shared by GitHub-owned models
apps/connections/   models.py admin.py factories.py migrations/0001_initial.py
apps/catalog/       models.py normalize.py setting_defs.py services.py admin.py factories.py
                    migrations/0001_initial.py 0002_app_setting_defaults.py
apps/activity/      models.py admin.py factories.py migrations/0001_initial.py
apps/ai_detection/  models.py admin.py factories.py migrations/0001_initial.py
apps/policy/        models.py services.py admin.py factories.py migrations/0001_initial.py
apps/metrics/       models.py admin.py factories.py migrations/0001_initial.py
apps/churn/         models.py admin.py factories.py migrations/0001_initial.py
apps/github_sync/   models.py admin.py factories.py migrations/0001_initial.py
apps/accounts/      models.py admin.py factories.py migrations/0003_userprojectaccess.py 0004_admin_permissions.py
tests/test_admin.py tests/test_factories.py tests/test_model_i18n.py
locale/uk/LC_MESSAGES/django.po|.mo     # all new verbose names and choice labels
static/css/app.css  static/css/.build-manifest.sha256
docs/CONFIGURATION.md                   # AppSetting key reference
```

---

## Design

### Module layout

One app per bounded domain, exactly the split in `.autodev/ARCHITECTURE.md` ("Components"). Every app gets
`__init__.py`, `apps.py` (`default_auto_field = BigAutoField`, `name = "apps.<x>"`, `label = "<x>"` — copying
`apps/accounts/apps.py`), `models.py`, `admin.py`, `factories.py`, `migrations/__init__.py`, `tests/__init__.py`.
`services.py` / `selectors.py` are added only where this phase actually needs one (`catalog`, `policy`), so no
empty module has to be typed by mypy.

Dependency direction is acyclic and one-way, which is why the migrations can be ordered:

```
connections ──▶ catalog ──▶ activity ──▶ ai_detection
                   │           ├──▶ policy ◀── catalog (SensitivePathRule.project)
                   │           └──▶ churn
                   ├──▶ github_sync (SyncRun.repositories M2M)
                   └──▶ accounts (UserProjectAccess.project)
metrics: no FK at all (DailyRollup.scope_id is a plain integer)
```

Cross-app FKs use **string references** (`"catalog.Repository"`) so no `models.py` imports another app's
`models.py`. Cross-app **enums** are imported directly (e.g. `ai_detection` will import `activity.AIStatus` in
phase 5); that is a value import, not a model import, and stays cycle-free because the arrows above are one-way.

### Field conventions

- **`github_id`** is `CharField(max_length=100)` holding the GraphQL **node ID** (an opaque base64 string), not an
  integer — GraphQL never returns a numeric id. Unique where spec §4 demands it and where GitHub always supplies
  it: `Organization`, `Repository`, `PullRequest`, `Commit`, `Review`, `ReviewComment`. `Identity.github_id` is
  nullable and **not** unique (a `git_email` identity has no node id; its contract is `(kind, value)`).
- **`raw = JSONField(null=True, blank=True)`** on every GitHub-owned model. Writing it is gated by
  `settings.STORE_RAW_PAYLOADS` in the sync phase; the column is always present.
- **Timestamps.** Everything from GitHub is stored in UTC as given. Local bookkeeping columns are
  `created_at = DateTimeField(auto_now_add=True)` / `updated_at = DateTimeField(auto_now=True)` on the
  operator-owned models (`Project`, `Person`, `GitHubConnection`, `AIPolicy`, `SensitivePathRule`,
  `DetectionRule`, `AppSetting`, `PolicyViolation`). No day-boundary logic lives in a model — `REPORT_TIMEZONE`
  belongs to the metrics phase's single helper.
- **Missing value is `None`.** Every numeric field that GitHub may not supply (`additions`, `first_review_at`,
  `churn_ratio`, `DailyRollup.value`, …) is `null=True`, never `default=0` (spec §15).
- **`verbose_name` and every choice label** is wrapped in `gettext_lazy as _`, including `Meta.verbose_name` and
  `verbose_name_plural`. Enforced by a test (T14), not by review.
- All enums are `models.TextChoices` nested in the owning model (or module-level where two models share one, e.g.
  `metrics.ScopeType`, `metrics.Cohort`).

### Models

**`connections.GitHubConnection`** — `name` (unique), `kind` (`fine_grained_pat` / `classic_pat` / `github_app`
reserved), `owner_login` (blank for classic PAT), `token_encrypted` (`BinaryField`, null), `token_last4`,
`token_login`, `expires_at` (null), `status` (`unverified` / `ok` / `degraded` / `invalid` / `expired`, default
`unverified`), `last_checked_at`, `last_check_result` (`JSONField(default=dict)` — codes + params only, never a
rendered sentence), `rate_limit_remaining`, `rate_limit_reset_at`, `is_active`, `created_by` (FK user,
`SET_NULL`), `created_at`, `updated_at`, plus the reserved nullable GitHub-App trio `app_id`, `installation_id`,
`private_key_encrypted`. `__str__` returns `name` — never the token. No encryption logic here: the Fernet
accessor is the next phase's (ADR 0004); this phase only owns the column.

**`catalog`**

| Model | Fields / constraints |
|---|---|
| `Organization` | `login` (unique), `type` (`org`/`user`), `github_id` (unique), `is_active`, `raw` |
| `Repository` | `organization` (FK CASCADE), `connection` (FK **PROTECT**), `name`, `full_name` (unique), `github_id` (unique), `default_branch`, `is_private`, `is_archived`, `is_active`, `sync_since` (DateField, null), `last_synced_at` (null), `sync_cursor` (`JSONField(default=dict)`), `merge_strategy_hint`, `raw`; index on `(is_active, last_synced_at)` |
| `Project` | `name`, `slug` (unique), `description`, `repositories` (M2M to `Repository`, `related_name="projects"`, blank), `is_active`, `color` |
| `Person` | `display_name`, `is_active`, `is_bot`, `exclude_from_metrics`, `team` (blank), `role_hint` (`dev`/`qa`/`design`/`other`, blank), `notes` (TextField, blank); index on `(is_active, is_bot)` |
| `Identity` | `person` (FK null, `SET_NULL`, `related_name="identities"`), `kind` (`github_login`/`git_email`), `value`, `github_id` (null, non-unique), `first_seen_at`; **`UniqueConstraint(kind, value)`**; index on `person` |
| `AppSetting` | `key` (unique), `value_type`, `value` (`JSONField`), `description`; `Meta.permissions = [("manage_settings", _("Can manage settings"))]` |

`Project.color` is **not** a hex string — a colour literal in `apps/**/*.py` fails
`tests/test_no_hardcoded_colors.py`. It is a `CharField` whose choices are the eight chart token *names* already
defined in `static/css/tokens.css` (`series-1` … `series-8`), default `series-1`; the template renders
`var(--{{ project.color }})`. A test asserts every choice exists as a `--<name>:` declaration in `tokens.css`, so
renaming a token cannot silently break a project badge.

`Identity` uniqueness only collapses duplicates if the value is normalised, and a duplicated identity is the
direct cause of risk #1 (a wrong number about a named person). `apps/catalog/normalize.py` holds the pure
function `normalize_identity_value(kind, value) -> str` (strip, casefold; GitHub logins and emails are both
case-insensitive), and `Identity.save()` calls it. It lives in its own module, not in `services.py`, because
`services.py` imports models and `models.py` must import the normaliser — a separate pure module keeps that
acyclic.

**`catalog` AppSetting typing.** `apps/catalog/setting_defs.py` holds a frozen registry — the code-side source of
truth required by `.autodev/ARCHITECTURE.md` ("typed rows, editable in the UI, with defaults in code"):

```python
@dataclass(frozen=True)
class SettingDef:
    key: str
    value_type: str  # bool | int | float | str | list | dict
    default: object
    description: StrOrPromise  # gettext_lazy
    group: str  # sync | metrics | ai | policy | churn | ui | export


SETTING_DEFS: tuple[SettingDef, ...] = ...
```

Keys and §15/§8/§9 defaults (one row each, seeded by migration `catalog/0002_app_setting_defaults.py`):

| group | key | type | default |
|---|---|---|---|
| sync | `BACKFILL_DAYS` | int | `180` |
| sync | `DEFAULT_CONNECTION_KIND` | str | `"fine_grained_pat"` |
| sync | `CONNECTION_CHECK_INTERVAL_HOURS` | int | `24` |
| ai | `AI_COHORT_INCLUDE_SUSPECTED` | bool | `False` |
| ai | `BOT_LOGIN_SUFFIXES` | list | `["[bot]"]` |
| ai | `BOT_LOGINS` | list | `["dependabot", "renovate", "github-actions"]` |
| ai | `DISCLOSURE_SECTION_HEADINGS` | list | `["AI assistance"]` |
| ai | `DISCLOSURE_LABELS_NONE` | list | `["None"]` |
| ai | `DISCLOSURE_LABELS_PARTIAL` | list | `["Partial"]` |
| ai | `DISCLOSURE_LABELS_SUBSTANTIAL` | list | `["Substantial"]` |
| ai | `DISCLOSURE_TOOLS_LABELS` | list | `["AI tools used"]` |
| metrics | `MIN_SAMPLE` | int | `5` |
| metrics | `STALE_DAYS` | int | `5` |
| metrics | `WAITING_REVIEW_HOURS` | int | `24` |
| metrics | `DURATION_MODE` | str | `"calendar_hours"` |
| metrics | `PR_SIZE_BUCKETS` | dict | `{"XS": 10, "S": 100, "M": 400, "L": 1000}` |
| metrics | `EXCLUDED_PATH_GLOBS` | list | lock files, `*.min.js`, `*.min.css`, `**/migrations/**`, `**/generated/**`, `**/vendor/**` |
| metrics | `TEST_PATH_GLOBS` | list | `tests/**`, `test_*.py`, `*_test.py`, `*.test.ts(x)`, `*.spec.ts(x)`, `*Tests.swift`, `**/__tests__/**` |
| metrics | `RUBBER_STAMP_MAX_MINUTES` | int | `10` |
| metrics | `REVIEW_LOAD_TOP_N` | int | `2` |
| metrics | `FOLLOWUP_FIX_WINDOW_DAYS` | int | `14` |
| metrics | `FOLLOWUP_FIX_FILE_OVERLAP` | float | `0.5` |
| policy | `NO_TESTS_MIN_LINES` | int | `20` |
| churn | `CHURN_WINDOW_DAYS` | int | `21` |
| churn | `CHURN_MAX_FILES` | int | `50` |
| ui | `DEFAULT_UI_LANGUAGE` | str | `"en"` |
| ui | `DETECT_BROWSER_LANGUAGE` | bool | `False` |
| ui | `DEFAULT_THEME` | str | `"system"` |
| export | `EXPORT_DURATION_UNIT` | str | `"hours"` |
| export | `EXPORT_SYNC_MAX_ROWS` | int | `20000` |

`apps/catalog/services.py` (typed, mypy-checked) exposes:

```python
def get_setting(key: str) -> object            # row → validated value; no row → the code default
def get_int(key) / get_bool(key) / get_str(key) / get_list(key) / get_dict(key)   # typed accessors
def set_setting(key: str, value: object) -> AppSetting   # validates type, rejects unknown key
def seed_app_settings(model: type) -> int      # idempotent get_or_create; used by the data migration too
```

`get_setting` on an **unknown** key raises `UnknownSettingError(KeyError)` — a typo must fail loudly, not return
`None`. `get_setting` on a **known** key whose row is missing returns the code default rather than raising, so a
half-applied database can still render a dashboard. `set_setting` and `AppSetting.clean()` validate the value
against `value_type` and raise `ValidationError` otherwise. `seed_app_settings` takes the model class as an
argument so the historical `apps.get_model("catalog", "AppSetting")` inside the data migration and the real model
in tests run the exact same code.

`DEFAULT_THEME` / `DEFAULT_UI_LANGUAGE` duplicate the `UserPreference` field defaults; §15 names both, so both
exist, and a test asserts the two agree (the failure mode is silent divergence, not absence).

**`activity`**

| Model | Notes |
|---|---|
| `PullRequest` | spec §4.2 raw fields + the derived cache (`size_bucket`, `is_revert`, `reverts_pr` self-FK `SET_NULL`, `is_hotfix`, `has_test_changes`, `ai_status`, `ai_tools` JSON, `ai_disclosure`, `is_rubber_stamp`, `is_self_merged`); `author`/`merged_by` FK → `Identity` `SET_NULL`; `UniqueConstraint(repository, number)`, `github_id` unique; indexes `(repository, merged_at)`, `(author, merged_at)`, `(state, is_draft, last_activity_at)`, `(ai_status, merged_at)`, `created_at` |
| `Commit` | `repository`, `sha`, `github_id` (unique, null), `author_identity`, `committer_identity`, `authored_at`, `committed_at`, `message`, `additions`, `deletions`, `co_authors` JSON, `trailers` JSON; `UniqueConstraint(repository, sha)`; index `(repository, committed_at)` |
| `PullRequestCommit` | `pull_request`, `commit`, `position`; `UniqueConstraint(pull_request, commit)`; `ordering = ["position"]` |
| `PRFile` | `pull_request`, `path`, `status`, `additions`, `deletions`, `is_test`, `is_excluded`, `matched_sensitive_rule` (FK `policy.SensitivePathRule`, null, `SET_NULL`); `UniqueConstraint(pull_request, path)`; index `(pull_request, is_excluded)` |
| `Review` | `pull_request`, `reviewer`, `state`, `submitted_at`, `body_length`, `comments_count`, `github_id` unique; indexes `(pull_request, submitted_at)`, `(reviewer, submitted_at)` |
| `ReviewComment` | `pull_request`, `author`, `created_at`, `is_review_thread`, `body_length`, `github_id` unique. **No comment text is stored** (spec §4.2) |
| `CheckStatus` | `pull_request`, `commit_sha`, `is_first_ci_commit`, `rollup_state`, `observed_at`; `UniqueConstraint(pull_request, commit_sha)`; index `(pull_request, is_first_ci_commit)` |

`ai_status` choices are `AIStatus` = `ai_explicit` / `ai_disclosed` / `ai_suspected` / `no_ai` / `unknown`
(spec §6.3); `ai_disclosure` = `none` / `partial` / `substantial` / `missing` / `ambiguous` (§6.2), default
`missing`; `size_bucket` = `XS`/`S`/`M`/`L`/`XL` (§8.2), nullable until derived. `PRFile.matched_sensitive_rule`
is the one FK that points forward into `policy`, so `activity/0001_initial` depends on `policy/0001_initial`;
`policy` depends on `activity` for `PolicyViolation.pull_request`. Django resolves that with a two-step
migration (`policy/0001_initial` has no FK to `activity`; the `PolicyViolation` FK is added in
`policy/0002_policyviolation`) — chosen over dropping the FK, because a dangling rule id would silently mis-label
a sensitive path.

**`ai_detection`** — `DetectionRule` (`name`, `detector` (the eight of §6.1), `pattern`, `tool`
(`claude_code`/`copilot`/`cursor`/`codex`/`devin`/`gemini`/`aider`/`windsurf`/`chatgpt`/`other`), `confidence`
(`high`/`medium`/`low`), `is_active`, `notes`, timestamps; index `(is_active, detector)`) and `AISignal`
(`pull_request`, `commit` null, `rule` FK `PROTECT`, `tool`, `confidence`, `evidence`
(`CharField(max_length=200)`), `detected_at`; index `(pull_request, confidence)`). **No uniqueness constraint on
`AISignal`**: spec §4.3 defines none, and the same rule can legitimately match two commits of one PR. Re-running
detection deletes a PR's signals and rewrites them (phase 5's contract).

**`policy`** — `AIPolicy` (`allowed_tools` JSON, `require_disclosure`, `require_human_approval`,
`min_human_approvals`, `require_tests_for_ai_prs`, `ai_pr_max_effective_lines` (null), `effective_from`,
`created_at`; `ordering = ["-effective_from"]` — versioned, so "singleton" means "the newest row with
`effective_from <= now`", resolved by `policy/services.py::current_policy(at=None)` rather than a selector,
because it is global data and `CLAUDE.md`'s "every selector starts from `scope_for_user`" must not be weakened by
an exception); `SensitivePathRule` (`project` FK null = global, `glob`, `ai_mode`
(`forbidden`/`needs_extra_review`), `description`, `is_active`; index `(project, is_active)`);
`PolicyViolation` (`pull_request`, `rule_code` (the nine codes of §7), `severity`, `details_params` JSON,
`details_hash`, `status` (`open`/`acknowledged`/`waived`/`resolved`, default `open`), `resolved_by` FK user
`SET_NULL`, `resolution_comment`, `resolved_automatically` bool, `created_at`, `updated_at`;
**`UniqueConstraint(pull_request, rule_code, details_hash)`**; indexes `(status, severity)`,
`(rule_code, created_at)`).

`details_hash` is only meaningful with a canonical hashing rule, so it ships with the constraint:
`policy/services.py::details_hash(params: Mapping[str, object]) -> str` = sha256 over
`json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)`, hex, `max_length=64`. Without
`sort_keys` the same violation evaluated twice would produce two rows and defeat risk #6's whole mitigation, so
the function and its ordering test belong to this phase even though the evaluators do not.

**`metrics.DailyRollup`** — `date`, `scope_type` (`global`/`project`/`repo`/`person`), `scope_id`
(`PositiveIntegerField(null=True)` — null for global, per spec §4.6), `cohort` (`all`/`ai`/`non_ai`),
`metric_key` (`CharField`, no FK: the registry lives in code), `value` (`FloatField(null=True)`), `sample_size`
(`PositiveIntegerField(default=0)`), `computed_at`; indexes `(metric_key, date)`,
`(scope_type, scope_id, date)`.

The documented constraint `UniqueConstraint(date, scope_type, scope_id, cohort, metric_key)` **does not cover the
global rows**: SQL treats two `NULL` `scope_id` values as distinct, so two global rows for the same day and metric
would both be accepted and `compute()` would double-count. A second **partial** constraint
`UniqueConstraint(date, scope_type, cohort, metric_key, condition=Q(scope_id__isnull=True),
name="uniq_dailyrollup_global")` closes the hole; partial unique indexes are supported by both SQLite and
PostgreSQL, so ADR 0001's portability rule holds. Both constraints get their own test.

**`churn.ChurnResult`** — `pull_request`, `window_days`, `lines_at_merge` (null), `lines_surviving` (null),
`churn_ratio` (`FloatField(null=True)`), `snapshot_sha`, `computed_at`, `status`
(`ok`/`unsupported_merge_method`/`too_large`/`error`), `error` (TextField blank);
`UniqueConstraint(pull_request, window_days)`; index `(status, computed_at)`.

**`github_sync.SyncRun`** — `started_at`, `finished_at` (null), `trigger` (`cli`/`ui`/`schedule`), `status`
(`running`/`success`/`partial`/`failed`), `repositories` (M2M to `catalog.Repository`, blank), `stats`
(`JSONField(default=dict)`), `stats_by_connection` (`JSONField(default=dict)`), `error_log` (TextField blank);
`ordering = ["-started_at"]`; index `started_at`. The masking of `error_log` is the logging filter's job
(installed in phase 1) plus the sync phase's writer; the column carries no secret of its own.

**`accounts.UserProjectAccess`** — `user` FK CASCADE, `project` FK `catalog.Project` CASCADE, `created_at`;
`UniqueConstraint(user, project)`. Migration `0004_admin_permissions` grants `catalog.manage_settings` to the
`admin` group (reverse removes it); `lead` keeps no model permission — its rights are view-level, per ADR 0002.
`scope_for_user()` is **not** changed: it still returns an unrestricted `ScopeFilter`, and narrowing it is the
scoped-access phase's job (roadmap #9). Shipping the table now is what lets that phase be a pure behaviour change.

### Admin

`config/admin.py` gains `ReadOnlyAdminMixin`: `has_add_permission`/`has_change_permission`/
`has_delete_permission` → `False`, `get_readonly_fields` → every concrete field. It lives in `config` because it
is cross-cutting and `config` already hosts `logging_filters.py`, `security.py`, `db.py`; a new shared app for
one mixin is not worth an `INSTALLED_APPS` entry. Consequence, and it is the intended one: a GitHub-owned
model's changelist answers **200** (superusers hold the view permission) while its add form answers **403**.

- **Read-only (GitHub-owned, per ARCHITECTURE "Identity and ownership"):** `Organization`, `Repository`,
  `PullRequest`, `Commit`, `PullRequestCommit`, `PRFile`, `Review`, `ReviewComment`, `CheckStatus`, `AISignal`,
  `DailyRollup`, `ChurnResult`, `SyncRun`, `AuditEntry` (an audit trail nobody may edit).
- **Editable (operator-owned):** `Project`, `Person`, `Identity`, `AppSetting`, `DetectionRule`, `AIPolicy`,
  `SensitivePathRule`, `PolicyViolation`, `UserProjectAccess`, `UserPreference`.
- **`GitHubConnection`:** `exclude = ("token_encrypted", "private_key_encrypted")`, `token_last4` and
  `token_login` read-only, `has_add_permission = False` (connections are created through the app's own form in the
  next phase, which is where encryption lives — ADR 0004 also says the admin "omits the token field entirely").
  `__str__` and every `list_display` entry is token-free.
- `Person.notes` is **not** in any `list_display` (risk: private 1:1 notes in a screenshot); it stays on the
  change form only.
- FK-heavy admins use `raw_id_fields` rather than a select rendering every identity, and `list_select_related`
  where `list_display` crosses a FK, so no admin page is an N+1.

### Error handling

- Schema-level invariants are DB constraints, not service checks: the duplicate tests assert `IntegrityError`
  inside `transaction.atomic()`.
- `AppSetting.clean()` + `set_setting()` raise `ValidationError` on a type mismatch; `get_setting()` raises
  `UnknownSettingError` on an unregistered key and falls back to the code default on a missing row.
- Deleting a `GitHubConnection` that still has repositories raises `ProtectedError` — the point of the `PROTECT`
  FK (ARCHITECTURE "What must never be lost", item 4). Tested.
- No model raises on data from GitHub: a field GitHub omits is `None`, and shape validation belongs to
  `github_sync.client`.

### How this honours the architecture

Same app split, same ownership table, same two "rules later phases must not break" (additive-only `DailyRollup`;
`rule_code` + params with no rendered text — enforced here by the *absence* of any message column on
`PolicyViolation` and `GitHubConnection.last_check_result`). Deviations, all logged to `.autodev/DECISIONS.md`:
the extra partial unique constraint on global `DailyRollup` rows, `github_id` as an opaque `CharField`,
`Project.color` as a token name, `AISignal` without a uniqueness constraint, the split
`policy/0001` + `policy/0002` migration pair, `current_policy()` in `services.py` rather than `selectors.py`, and
`normalize_identity_value` in its own `normalize.py`.

---

## Tasks

- [x] **T1: App skeletons.** Create `apps/{connections,catalog,activity,ai_detection,policy,metrics,churn,github_sync}/`
  with `__init__.py`, `apps.py`, empty `models.py`, `admin.py`, `migrations/__init__.py`, `tests/__init__.py`;
  add all eight to `INSTALLED_APPS` in `config/settings/base.py`. Test: `tests/test_installed_apps.py` asserts
  every app label in the architecture's component table is installed and that `apps.get_app_config(label).name`
  starts with `apps.`. Check: `uv run python manage.py check`.
- [x] **T2: `connections.GitHubConnection`.** Model + `0001_initial`. Tests
  (`apps/connections/tests/test_models.py`): duplicate `name` raises `IntegrityError`; `status` defaults to
  `unverified`; `last_check_result` defaults to `{}`; `__str__` contains the name and not `token`; the reserved
  GitHub-App fields are nullable.
- [x] **T3: `catalog` core models.** `Organization`, `Repository`, `Project`, `Person`, `Identity` +
  `normalize.py` + `0001_initial` (depends on `connections.0001`). Tests
  (`apps/catalog/tests/test_models.py`): `Identity` rejects a duplicate `(kind, value)`; `Identity.save()`
  normalises `"Foo"` → `"foo"` so the second insert collides; `Repository.full_name` unique; deleting a
  `GitHubConnection` with a repository raises `ProtectedError`; a repository can belong to two projects;
  every `Project.color` choice exists as a `--<name>:` declaration in `static/css/tokens.css`.
- [x] **T4: `AppSetting` + typed settings registry.** `catalog/setting_defs.py`, `AppSetting` model (with the
  `manage_settings` permission), `catalog/services.py`, data migration `catalog/0002_app_setting_defaults.py`.
  Tests (`apps/catalog/tests/test_app_settings.py`): every `SETTING_DEFS` key exists after migration with a value
  equal to its default and a matching `value_type`; `seed_app_settings` is idempotent (second call creates 0 rows
  and overwrites no operator edit); `get_setting` on an unknown key raises `UnknownSettingError`; on a deleted row
  returns the code default; `set_setting` with a wrong type raises `ValidationError`; typed accessors return the
  right Python types; `DEFAULT_THEME` / `DEFAULT_UI_LANGUAGE` equal the `UserPreference` field defaults.
- [x] **T5: `policy` models (part 1).** `AIPolicy`, `SensitivePathRule` + `0001_initial` (depends on
  `catalog.0001`, no FK to `activity`), and `policy/services.py` with `details_hash()` and `current_policy()`.
  Tests (`apps/policy/tests/test_models.py`, `test_services.py`): `details_hash` is stable under key reordering
  and differs when a value changes; `current_policy()` returns the newest row with `effective_from <= now`
  (freezegun) and `None` when none applies; a global `SensitivePathRule` has `project is None`.
- [x] **T6: `activity` models.** All seven models + `0001_initial` (depends on `catalog.0001` and `policy.0001`).
  Tests (`apps/activity/tests/test_models.py`): `(repository, number)`, `(repository, sha)`,
  `(pull_request, path)`, `(pull_request, commit_sha)` and `(pull_request, commit)` duplicates raise
  `IntegrityError`; `ai_status` defaults to `unknown` and `ai_disclosure` to `missing`; nullable duration source
  fields default to `None`, not `0`; `ReviewComment` has no body/text field (asserted over `_meta.fields`, so a
  later phase cannot quietly start storing comment text).
- [x] **T7: `policy.PolicyViolation` (part 2).** `0002_policyviolation` (depends on `activity.0001`). Tests: the
  `(pull_request, rule_code, details_hash)` duplicate raises `IntegrityError`; two different `details_hash`
  values for one `(pr, rule_code)` coexist; `status` defaults to `open`; no field on the model stores a rendered
  message (assert the field names against an explicit allowlist).
- [x] **T8: `ai_detection` models.** `DetectionRule`, `AISignal` + `0001_initial`. Tests: `evidence` longer than
  200 characters fails `full_clean`; `rule` is `PROTECT` (deleting a rule with signals raises `ProtectedError`);
  two signals from one rule on two commits of one PR are both accepted.
- [x] **T9: `metrics.DailyRollup`.** Model + `0001_initial`. Tests
  (`apps/metrics/tests/test_models.py`): a duplicate `(date, scope_type, scope_id, cohort, metric_key)` for a
  project scope raises `IntegrityError`; a duplicate **global** row (`scope_id=None`) also raises
  `IntegrityError` (the partial constraint); the same metric for two cohorts, two dates or two scope ids coexists;
  `value=None` is storable and distinct from `0.0`.
- [x] **T10: `churn.ChurnResult`.** Model + `0001_initial`. Tests: duplicate `(pull_request, window_days)` raises
  `IntegrityError`; two different windows for one PR coexist; `status` choices cover the four spec values and
  `error` is blank by default.
- [x] **T11: `github_sync.SyncRun`.** Model + `0001_initial`. Tests: `stats`/`stats_by_connection` default to
  `{}`; `repositories` M2M accepts zero and many; `ordering` puts the newest run first; `error_log` is blank by
  default.
- [x] **T12: `accounts.UserProjectAccess` + the admin-group grant.** `0003_userprojectaccess`,
  `0004_admin_permissions`. Tests (`apps/accounts/tests/test_models.py`, `test_permissions.py`): duplicate
  `(user, project)` raises `IntegrityError`; the `admin` group has `catalog.manage_settings` and the `lead` group
  does not; `scope_for_user()` still returns an unrestricted filter even when access rows exist (the behaviour
  change is a later phase's, and this pins it so the deferral is deliberate rather than forgotten).
- [x] **T13: factories for every model.** `apps/<app>/factories.py` using `factory.django.DjangoModelFactory`,
  with `django_get_or_create` on the natural keys and `Sequence` on the unique ones, and cross-app `SubFactory`
  chains (`PullRequestFactory` → `RepositoryFactory` → `OrganizationFactory` + `GitHubConnectionFactory`).
  Tests (`tests/test_factories.py`): every concrete model in the project's apps has exactly one factory (walk
  `django.apps.apps.get_models()` against a discovered factory registry — deny-by-default, so a model added in a
  later phase without a factory fails the build); every factory `.create()`s an instance that passes
  `full_clean()`; calling each factory twice does not raise.
- [x] **T14: admin for every model.** `config/admin.py` with `ReadOnlyAdminMixin`; `admin.py` in all eight new
  apps plus `UserProjectAccess` in `accounts`; `AuditEntry` switched to read-only. Tests (`tests/test_admin.py`):
  every concrete project model is registered (deny-by-default walk, same shape as T13); as a superuser, every
  registered changelist returns 200; the add view returns 200 for an explicit editable list and 403 for an
  explicit read-only list, and the union of the two lists equals the registry (so a new model must be classified,
  not defaulted); a read-only admin's `get_readonly_fields()` covers every editable field; the
  `GitHubConnectionAdmin` form's `base_fields` contain no key matching `token` or `private_key`, and the rendered
  changelist HTML contains neither; `Person.notes` is absent from `PersonAdmin.list_display`.
- [x] **T15: model i18n guard.** `tests/test_model_i18n.py`: for every concrete field of every project model
  (skipping auto-created and reverse fields), `field.verbose_name` is a lazy translation proxy, and every choice
  label is too; the same for `Meta.verbose_name` / `verbose_name_plural`. Fix whatever it catches.
- [x] **T16: Ukrainian translations.** `make messages`, translate every new msgid (model names, verbose names,
  choice labels, `AppSetting` descriptions) in `locale/uk/LC_MESSAGES/django.po`, no fuzzy entries, commit `.po`
  **and** `.mo`. Verified by the existing `tests/test_translations.py`.
- [x] **T17: regenerate the committed CSS manifest.** `make css`, commit `static/css/app.css` and
  `static/css/.build-manifest.sha256` (the new `apps/**/*.py` files are inside the manifest's scan set).
  Verified by `tests/test_css_tokens.py::test_app_css_is_not_stale`.
- [x] **T18: docs.** `docs/CONFIGURATION.md` AppSetting table, `docs/PROGRESS.md` phase-2 entry, and
  `.autodev/DECISIONS.md` `## p02-implement` bullets all done. `CHANGELOG.md` gets no entry: nothing
  user-visible ships.
- [x] **T19: gate.** Ran the full verification block from a clean tree, including `migrate` against a throwaway
  empty database file. Fixed along the way: `ruff` findings across the new apps (DJ001 `# noqa` on four
  intentionally-nullable `CharField`s per the plan's own design — `GitHubConnection.app_id`/`installation_id`,
  `Identity.github_id`, `PullRequest.size_bucket`; DJ012 method order in `catalog.Identity`; ~40 `E501` lines,
  mostly fixed by `ruff format`), and a real gap in `tests/test_admin.py::test_every_model_is_registered_and_classified`
  found during a decisions-log review (it never walked `apps.get_models()`, so an unregistered model would pass
  silently) — now asserts against a `get_models()` walk like `test_factories.py` does.

---

## Verification

```bash
# migrations from zero, on a database that does not exist yet
rm -f /tmp/pr-radar-zero.sqlite3
DATABASE_URL=sqlite:////tmp/pr-radar-zero.sqlite3 uv run python manage.py migrate --noinput
DATABASE_URL=sqlite:////tmp/pr-radar-zero.sqlite3 uv run python manage.py makemigrations --check --dry-run
rm -f /tmp/pr-radar-zero.sqlite3

# the project gate (CLAUDE.md "lint" + "test")
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py check
uv run pytest -q

# generated artefacts this phase touches
make css && git diff --stat static/css/app.css static/css/.build-manifest.sha256   # must be empty after commit
make messages && git diff --stat locale/                                            # must be empty after commit
```

| # | Acceptance criterion | Test that proves it |
|---|---|---|
| 1 | `migrate` on an empty database succeeds | the `DATABASE_URL=sqlite:////tmp/pr-radar-zero.sqlite3 … migrate` command above, plus `pytest` itself (pytest-django builds the test DB by running every migration) |
| 2 | `makemigrations --check --dry-run` reports no pending changes | the gate command above (T19), run in CI-shape by the orchestrator |
| 3 | Every registered admin changelist and add form answers 200, or 403 where the model is read-only | `tests/test_admin.py::test_every_changelist_returns_200`, `::test_add_view_is_200_for_editable_models`, `::test_add_view_is_403_for_read_only_models`, `::test_every_model_is_registered_and_classified` |
| 4 | The `GitHubConnection` admin form exposes no token field | `tests/test_admin.py::test_github_connection_admin_form_has_no_token_field` (form `base_fields` + rendered changelist HTML) |
| 5 | `DailyRollup`, `PolicyViolation` and `Identity` reject duplicates on their documented constraints | `apps/metrics/tests/test_models.py::test_duplicate_rollup_rejected` and `::test_duplicate_global_rollup_rejected`; `apps/policy/tests/test_models.py::test_duplicate_violation_rejected`; `apps/catalog/tests/test_models.py::test_duplicate_identity_rejected` and `::test_identity_value_is_normalised_before_uniqueness` |
| 6 | Every `AppSetting` default from spec §15 is present after migration | `apps/catalog/tests/test_app_settings.py::test_every_setting_default_is_seeded_by_migration` (parametrized over `SETTING_DEFS`) |
| — | Deliverable: factories for every model, reused later | `tests/test_factories.py::test_every_model_has_a_factory`, `::test_every_factory_creates_a_valid_instance` |
| — | Deliverable: `gettext_lazy` on every verbose name and choice label | `tests/test_model_i18n.py` |
| — | Convention: no colour literal enters `apps/` (`Project.color`) | existing `tests/test_no_hardcoded_colors.py` + `apps/catalog/tests/test_models.py::test_project_colors_are_declared_tokens` |
| — | Convention: Ukrainian parity in the same phase | existing `tests/test_translations.py` |

### Case selection (per `.autodev/guides/case-taxonomy.md`)

This phase has no user flow, so the taxonomy collapses onto three axes: **input and boundaries** (`AppSetting`
type validation, 200-character `evidence`, `None` vs `0`), **idempotency and repetition** (re-running the seed
migration, re-creating a factory, duplicate inserts on every documented constraint — the phase's core risk), and
**permissions** (superuser add/change against read-only admins, the `admin`-vs-`lead` permission split, with the
positive case beside every refusal as the guide requires). Deliberately **not** authored, with reasons:
navigation, async chains, accessibility, platform sanity and concurrency — there is no view, no task, no template
and no second writer in this phase; e2e cases — the phase ships no user-visible surface, so
`e2e/surfaces.toml` gains nothing.

---

## Risks

| Risk | What this plan does |
|---|---|
| **#1 wrong number about a named person** | `Identity` uniqueness is enforced *after* normalisation, so `Foo`/`foo` cannot become two people; `Identity.person` is nullable with `SET_NULL` so an unmapped identity is a queue item, never a silent drop or a cascade delete; `Person.is_bot` / `exclude_from_metrics` / `notes` exist from day one, and `notes` is kept out of every `list_display` |
| **#2 token leak** | `token_encrypted` is a `BinaryField` with no plaintext column; the admin excludes it and `private_key_encrypted`; `GitHubConnection.__str__` and every `list_display` are token-free; T14 asserts the absence in both the form and the rendered HTML. Encryption itself is the next phase's (ADR 0004) |
| **#3 cross-project data leak** | `UserProjectAccess` ships with a `(user, project)` constraint, but `scope_for_user()` is deliberately unchanged and a test pins that, so the scoped-access phase makes one behaviour change in one place instead of discovering a missing table |
| **#6 duplicated / resurrected violations** | the `(pull_request, rule_code, details_hash)` constraint **plus** the canonical `details_hash()` function and its key-order test — the constraint alone would not have stopped a re-run from writing a second row; `resolved_automatically` is a separate column so auto-resolve can never be confused with a human decision |
| **#7 metric correctness** | `DailyRollup` gets no `kind` column and no FK to a metric: only additive rows belong there (ADR 0007), and the extra partial unique constraint stops a global row from being counted twice. No timezone logic enters a model |
| **#8 Ukrainian lags** | T15 makes lazy `verbose_name`s a test, not a habit; T16 translates every new msgid in this phase and the existing `.po`/`.mo` freshness tests fail otherwise |
| **#10 dashboard latency** | the indexes listed in *Design* are created with the tables (cheaper than an `ALTER` in the polish phase), and FK-heavy admins use `raw_id_fields` / `list_select_related` so no admin page is an N+1 |
| **#11 churn fragility** | `ChurnResult.status` + `error` make a failure data rather than an exception, and the `(pull_request, window_days)` constraint lets the churn phase re-run safely |
| **#14 SQLite contention** | nothing new: no migration here is long-running, and the partial unique index is portable to PostgreSQL (ADR 0001) |
| **#15 stale generated artefact** | T16 and T17 regenerate `.po`/`.mo` and `app.css` + its checksum in this phase, and the existing freshness tests are part of the gate |

---

## Out of scope

- **Token encryption, connection verification, the GraphQL client, discovery, `sync`, `SyncRun` writing,
  `bootstrap_connection`, `rotate_encryption_key`** → roadmap #3. This phase owns the columns only.
- **Identity auto-mapping, the unmapped-identity queue, bot detection, `activity.derive`** → #4. The derived
  columns exist and stay `None` / default.
- **`fixtures/detection_rules.yaml`, `seed_detection_rules`, the eight detectors, the disclosure parser,
  `ai_status` resolution** → #5. `DetectionRule` ships empty; only the `AppSetting` disclosure synonyms are seeded.
- **The nine policy evaluators, auto-resolve, the violations console** → #6. `details_hash()` ships; nothing calls
  it yet.
- **`MetricDef` registry, calculators, `compute()`, `recompute`, `metrics_doc`, `docs/METRICS.md`** → #7.
  `DailyRollup.metric_key` is a free `CharField` until the registry exists.
- **Every page, chart, table, filter, CSV/XLSX export, `ExportJob`, `seed_demo`** → #8/#9. No template, no URL,
  no `selectors.py` beyond what already exists.
- **Narrowing `scope_for_user()` by `UserProjectAccess`** → #9, asserted there across pages, chart JSON, CSV and
  XLSX separately.
- **Churn service, clones, `GIT_ASKPASS`, `compute_churn`** → #10.
- **Index tuning against 50 repos / 20 k PRs, `assertNumQueries` on list views, empty states, `MIN_SAMPLE`
  greying** → #11 (and #8 for the UI half). The index *set* is created now; profiling is not.
- **`tests/test_logging.py`**, untracked because the orchestrator held it back from the phase-1 commit over a
  token-shaped literal: left exactly as found. Deciding whether to gitignore it or scrub and commit it belongs to
  whoever owns that phase-1 loose end, not to a data-model phase.
