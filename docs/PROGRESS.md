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
