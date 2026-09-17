# Progress

What exists in the codebase right now, kept current as phases land. For the full phase-by-phase plan see
`.autodev/ROADMAP.md`; for the reasoning behind a specific choice see `docs/DECISIONS.md` and `docs/dev/adr/`.

## Phase 1 — Skeleton, auth, i18n and design tokens

Delivers a runnable, logged-in, bilingual, themed empty application. Everything below works from a clean
checkout with `uv sync && uv run python manage.py migrate`.

- **Project shell**: `pyproject.toml`/`uv.lock`, `manage.py` (defaults to `config.settings.local`),
  `config/settings/{base,local,prod,e2e}.py`, SQLite in WAL mode with `busy_timeout=5000` and
  `foreign_keys=ON` (`config/db.py`), huey queued on its own `DATA_DIR/huey.sqlite3` file.
- **Secrets**: `config/security.py::mask_secrets` + `config/logging_filters.py::SecretMaskingFilter` on the root
  logger, masking every GitHub token shape before it can reach a log line.
- **`accounts` app**: `UserPreference` (theme, language), `AuditEntry` (actor, code, before/after JSON),
  `ScopeFilter`/`scope_for_user()` (unrestricted in this phase — every future selector already takes one),
  `record_audit`/`set_theme`/`set_language` services, the `admin`/`lead` groups (empty until phase 2).
- **Auth**: Django's login/logout/password-reset views and templates, `LoginRequiredMiddleware` so every URL
  except that allowlist redirects an anonymous visitor to `/accounts/login/`.
- **`dashboards` app**: a single placeholder Overview page at `/`, the post-login landing page and the home
  the navigation points at until later phases add real pages.
- **Theme**: `UserPreference.theme` → cookie → `system` resolution order, a context processor, a pre-paint
  inline script in `<head>` so there is no flash, `static/js/theme.js` wiring the switcher.
- **i18n**: English + Ukrainian with `LocaleMiddleware` and a custom `UserLanguageMiddleware` that makes the
  stored preference win over `Accept-Language`; `JavaScriptCatalog` at `/jsi18n/`; a duration formatter
  implemented once in Python (`apps/dashboards/formatting.py`) and once in JS
  (`static/js/formatting.js`), proven to agree via the shared fixture `tests/fixtures/duration_cases.json`.
- **Design tokens**: `static/css/tokens.css` is the only file allowed a colour literal (enforced by a grep
  test); Tailwind v4 standalone build, committed `static/css/app.css`.
- **Gates**: `uv run pytest -q` (unit/integration), the lint chain in `CLAUDE.md`, and
  `make e2e-up && uv run pytest e2e -q; make e2e-down` (login, theme persistence, language persistence, and
  JS/Python duration-formatter parity, driven with Playwright against `config.settings.e2e`).

### Out of scope for phase 1

Every domain model (`catalog`, `activity`, `ai_detection`, `policy`, `metrics`, `churn`, `connections`,
`github_sync`), any GitHub client code, real navigation targets beyond Overview, charts, tables and exports.
See `.autodev/phases/01-skeleton/PLAN.md`'s "Out of scope" section for the full list and which phase owns each.

## Phase 2 — Domain models, migrations and Django admin

Every table in spec §4 now exists, migrates from zero, and is visible in `/admin/` with the right read-only and
hidden fields. No new URL, template or page — the only human surface added is the admin.

- **Eight new apps**: `connections`, `catalog`, `activity`, `ai_detection`, `policy`, `metrics`, `churn`,
  `github_sync`, each with `models.py`, `admin.py`, `factories.py` and its own `migrations/0001_initial.py`
  (`policy` additionally ships `0002_policyviolation` after `activity.0001`, since the FK graph between the two
  apps is mutual).
- **`connections.GitHubConnection`**: the column set for a PAT or GitHub App connection, including
  `token_encrypted`/`private_key_encrypted` as `BinaryField`s with no plaintext column. Encryption itself and the
  GraphQL client land in the next phase.
- **`catalog`**: `Organization`, `Repository`, `Project` (with `color` as a chart-token name, not a hex literal),
  `Person`, `Identity` (uniqueness enforced after `normalize.py::normalize_identity_value` casefolds and strips
  the value), and `AppSetting` backed by the frozen `setting_defs.py::SETTING_DEFS` registry, seeded by a data
  migration and read through `catalog/services.py`'s typed accessors (`get_setting`/`get_int`/`get_bool`/…).
- **`activity`**: `PullRequest`, `Commit`, `PullRequestCommit`, `PRFile`, `Review`, `ReviewComment`,
  `CheckStatus` — the raw GitHub fields plus the derived-cache columns later phases fill in (`ai_status`,
  `size_bucket`, `is_rubber_stamp`, …), all defaulting to `None`/`unknown` rather than `0`/a rendered value.
- **`ai_detection`**: `DetectionRule`, `AISignal` (no uniqueness constraint — one rule may legitimately match
  two commits of a PR; re-running detection deletes and rewrites a PR's signals).
- **`policy`**: `AIPolicy` (versioned, resolved by `policy/services.py::current_policy()`), `SensitivePathRule`,
  `PolicyViolation` with `(pull_request, rule_code, details_hash)` uniqueness and the canonical
  `policy/services.py::details_hash()` (sha256 over a `sort_keys=True` JSON dump) that makes the constraint
  actually catch a re-evaluated duplicate.
- **`metrics.DailyRollup`**: additive-only rollup rows, with the documented unique constraint plus a second
  **partial** constraint for `scope_id IS NULL` rows (SQL treats two NULLs as distinct, so the global scope needs
  its own index to stay duplicate-free).
- **`churn.ChurnResult`**, **`github_sync.SyncRun`**: columns only, per spec §4; no service reads or writes them
  yet.
- **`accounts.UserProjectAccess`** (`(user, project)` unique) and the `admin` group's new
  `catalog.manage_settings` permission; `scope_for_user()` itself is deliberately left unrestricted — narrowing
  it is the scoped-access phase's job.
- **Admin**: `config/admin.py::ReadOnlyAdminMixin` makes every GitHub-owned model's changelist read-only (200 on
  list, 403 on add); `GitHubConnectionAdmin` excludes both encrypted fields and disables add entirely;
  `Person.notes` is excluded from every list display.
- **Factories**: one `factory.django.DjangoModelFactory` per concrete model (`tests/test_factories.py` enforces
  this by walking `django.apps.apps.get_models()`), each producing a `full_clean()`-valid instance.
- **i18n**: every `verbose_name` and choice label is `gettext_lazy`, enforced by `tests/test_model_i18n.py`;
  Ukrainian translations for all of it are in the same phase.
- **Docs**: `docs/CONFIGURATION.md` gained the full `AppSetting` reference table.

### Out of scope for phase 2

Token encryption, connection verification, the GraphQL client, `sync`/`SyncRun` writing, `bootstrap_connection`,
identity auto-mapping and bot detection, `fixtures/detection_rules.yaml` and the eight AI detectors, the nine
policy evaluators and the violations console, the `MetricDef` registry and `compute()`, the churn service and
`compute_churn`, every dashboard page/chart/table/export, and narrowing `scope_for_user()` by
`UserProjectAccess`. See `.autodev/phases/02-data-model/PLAN.md`'s "Out of scope" section for which later phase
owns each.
