# Autodev progress — PR Radar

- **Status:** running
- **Current:** phase 2/11 · step `commit`
- **Spec:** `docs/SPEC.md` · **Branch:** `autodev/spec-20260917-0714` · **PR:** https://github.com/korkholeh/pr-radar/pull/1
- **Stack:** Python 3.12 + Django 5.2 LTS + SQLite (WAL, ORM-only for Postgres portability) + httpx/GraphQL + huey/SqliteHuey + Django templates/htmx/Alpine + Tailwind standalone CLI (committed CSS) + vendored Chart.js + django-tables2/django-filter + XlsxWriter, tested with pytest/pytest-django/factory_boy/freezegun/respx, linted with ruff + mypy, managed by uv. · **Profile:** `django-htmx`
- **Test command:** `uv run pytest -q` · **E2E:** `uv run pytest e2e -q`
- **Usage:** 5h ? (reset 17.09 15:20) · 7d 23%
- **Totals:** 25 sessions · 2.7 h agent time · ≈$52.30 API-equivalent
- **Clock:** 3.5 h since the run was created · 2.7 h working · 0.8 h paused on the usage limit · 0.0 h not running
- **Updated:** 2026-09-17 10:46:37

## Phases

| # | Phase | User-facing | Status | Commit | Warnings |
|---|---|---|---|---|---|
| 1 | Skeleton, auth, i18n and design tokens | yes | ✅ done | e8a30ee | the audit of the round-2 fixes found blocker/major findings (see .autodev/phases/01-skeleton/REVIEW-r2-audit.md); the round-2 audit's own findings were fixed and not re-checked; not committed: tests/test_logging.py (it contains what looks like a GitHub token) |
| 2 | Domain models, migrations and Django admin | no | 🔨 in_progress |  |  |
| 3 | GitHub connections and incremental sync | yes | ⏳ pending |  |  |
| 4 | Identity resolution and derived PR fields | yes | ⏳ pending |  |  |
| 5 | AI detection | yes | ⏳ pending |  |  |
| 6 | AI policy engine and violations console | yes | ⏳ pending |  |  |
| 7 | Metrics registry, rollups and recompute | no | ⏳ pending |  |  |
| 8 | Dashboards, charts, themes and table export | yes | ⏳ pending |  |  |
| 9 | People, PRs, Reviews, scoped access and reports | yes | ⏳ pending |  |  |
| 10 | CI first-pass, follow-up fixes and churn | yes | ⏳ pending |  |  |
| 11 | Polish, performance and documentation | yes | ⏳ pending |  |  |

## Timeline

- `2026-09-17 07:26:52` **architect** — done (12m, $3.16): PR Radar is one Django 5.2 project over a single SQLite file, with one web process and one huey worker — no broker, no second service, no SPA. Ten apps per spec §3 sit behind three deliberate choke points: a `GitHubAuth` protocol so no code but `connections` ever holds a token, `scope_for_user()` s…
- `2026-09-17 07:26:55` **push** — done: korkholeh/pr-radar@main (base branch created for the pull request)
- `2026-09-17 07:26:56` **push** — done: korkholeh/pr-radar@autodev/spec-20260917-0714 as korkholeh
- `2026-09-17 07:26:59` **pr** — draft: https://github.com/korkholeh/pr-radar/pull/1
- `2026-09-17 07:32:09` **roadmap** — done (5m, $1.76): PR Radar is a local Django 5.2 tool that pulls pull-request data from GitHub's GraphQL API into SQLite and shows team leads dashboards on AI adoption, AI-policy compliance, and delivery quality at the global, project, repository and person level. The roadmap keeps spec §14's own eleven phases (0–10…
- `2026-09-17 07:32:12` **push** — done: korkholeh/pr-radar@autodev/spec-20260917-0714 as korkholeh
- `2026-09-17 07:38:32` **p01-plan** — done (6m, $2.26): Wrote .autodev/phases/01-skeleton/PLAN.md for phase 1 (skeleton, auth, i18n, design tokens) with Context, Design, 19 ordered tasks including their tests, a verification table mapping every acceptance criterion to a named test, risk coverage and out-of-scope. Appended 10 decision/verification bullet…
- `2026-09-17 07:53:05` **p01-implement** — handover: context reached 200,127 tokens — checkpointing for a fresh session
- `2026-09-17 07:53:29` **p01-implement** — partial (14m, $5.26): Built the full skeleton for phase 1: uv project (pyproject.toml, manage.py, .env.example), config/settings/{base,local,prod,e2e}.py with SQLite WAL pragmas, secret-masking logging, huey on its own SQLite file; apps/accounts (UserPreference, AuditEntry, ScopeFilter/scope_for_user, record_audit/set_t…
- `2026-09-17 08:05:53` **p01-implement** — done (12m, $2.42): Finished phase 1 (all 19 tasks). Prior session left T1–T15 done; this session completed T16 (verified Makefile already correct), T17 (built full e2e harness: surfaces.toml, conftest.py, support/{personas,auth}.py, plans/auth.plan.yaml, README.md, and specs test_login/test_theme/test_language/test_d…
- `2026-09-17 08:05:55` **p01-tests** — pass: exit 0: ....................... [100%]
- `2026-09-17 08:13:54` **p01-review1** — changes_requested (7m, $2.92): Phase 1 delivers a genuinely runnable, logged-in, bilingual, themed skeleton: I ran every gate myself and `uv run pytest -q` (95 tests), the full lint chain, a fresh `make css` (committed app.css is identical to a fresh build) and `make e2e-up && uv run pytest e2e -q; make e2e-down` (4 specs, exit …
- `2026-09-17 08:31:26` **p01-review_fix1** — done (17m, $4.19): Applied all blocker/major findings from REVIEW-r1 plus most minors and trivial nits. Fixed: - BLOCKER: root conftest.py now compiles locale/uk/*.po → *.mo via polib in a session-scoped autouse fixture, so a clean checkout (no committed .mo) passes tests. - MAJOR: huey immediacy now mutates the real…
- `2026-09-17 08:31:29` **p01-tests** — pass: exit 0: .................................... [100%]
- `2026-09-17 08:36:45` **p01-review2** — changes_requested (5m, $2.46): Phase 1 is in good shape: I ran every gate myself and all are green — `uv run pytest -q` (108 passed, including from a clean `git archive` copy that has no `.mo` files), the full lint chain, a fresh Tailwind build byte-identical to the committed `static/css/app.css` with a matching `.build-manifest…
- `2026-09-17 08:43:23` **p01-review_fix2** — done (6m, $1.61): All 4 REVIEW-r2 findings fixed, none rejected. MAJOR — Ukrainian dead on clean install: committed locale/uk/LC_MESSAGES/*.mo (dropped *.mo from .gitignore), removed the conftest.py fixture that silently recompiled .mo at test time, added test_mo_catalog_matches_committed_po_source (content-diff via…
- `2026-09-17 08:43:26` **p01-tests** — pass: exit 0: ......................................... [100%]
- `2026-09-17 08:49:30` **p01-review_audit2** — changes_requested (6m, $2.1): All four REVIEW-r2 findings (1 major, 3 minors) are really fixed in the product, each with a test I confirmed fails without the fix; nothing was rejected. I re-ran every gate: `uv run pytest -q` 113 passed, the full lint chain clean, and a clean `git archive` export of the staged tree now renders U…
- `2026-09-17 08:51:55` **p01-review_fix2** — done (2m, $0.54): Fixed both MAJOR findings and the MINOR finding from REVIEW-r2-audit; nothing rejected. - [MAJOR] `static/css/src/input.css`: `@import "tailwindcss" source(none);` disables Tailwind v4's whole-repo auto content-scan so only the manifest-covered `@source` paths affect the build. Reran `make css`; ve…
- `2026-09-17 08:51:58` **p01-tests** — pass: exit 0: ......................................... [100%]
- `2026-09-17 09:01:27` **p01-e2e** — done (9m, $1.96): QA pass on phase 1. Baseline was already green (118 unit tests, full lint chain, 4 e2e specs). Manual browser/curl exploration beyond the automated suite found a real bug: django.contrib.admin ships its own registration/{logged_out,password_reset_*}.html templates and, sitting before apps.accounts …
- `2026-09-17 09:01:29` **p01-e2e** — pass: exit 0: .... [100%]
- `2026-09-17 09:03:32` **p01-docs** — done (2m, $0.68): Verified phase-1 docs against actual code/Makefile/pyproject: CLAUDE.md, README.md, docs/SETUP.md, docs/CONFIGURATION.md, docs/TRANSLATIONS.md, docs/PROGRESS.md, docs/DECISIONS.md were already accurate — no edits needed there. Filled two real gaps required by this step: created CHANGELOG.md (Unrele…
- `2026-09-17 09:03:32` **commit** — held back: tests/test_logging.py (it contains what looks like a GitHub token)
- `2026-09-17 09:03:33` **commit** — command files: this commit changes what the project's own commands run: Makefile, pyproject.toml
- `2026-09-17 09:03:33` **p01-commit** — done: e8a30ee
- `2026-09-17 09:03:35` **push** — done: korkholeh/pr-radar@autodev/spec-20260917-0714 as korkholeh
- `2026-09-17 09:03:39` **p01-push** — done: korkholeh/pr-radar@autodev/spec-20260917-0714-p01-skeleton
- `2026-09-17 09:03:42` **p01-pr** — draft: https://github.com/korkholeh/pr-radar/pull/2
- `2026-09-17 09:03:48` **p01-merge** — done: https://github.com/korkholeh/pr-radar/pull/2
- `2026-09-17 09:12:38` **p02-plan** — done (8m, $2.9): Wrote .autodev/phases/02-data-model/PLAN.md: 19 ordered tasks covering the eight new apps' models, migrations, indexes and uniqueness constraints, the AppSetting registry with the §15 defaults, Django admin with a read-only mixin and a token-free GitHubConnection form, factories for every model, th…
- `2026-09-17 09:27:17` **p02-implement** — handover: context reached 200,961 tokens — checkpointing for a fresh session
- `2026-09-17 09:27:51` **p02-implement** — partial (15m, $5.94): Phase 2 (domain models, migrations, admin) is functionally complete: T1-T17 done and checked off in .autodev/phases/02-data-model/PLAN.md. Built all 8 new apps (connections, catalog, activity, ai_detection, policy, metrics, churn, github_sync) with models per spec §4, migrations from zero, factorie…
- `2026-09-17 09:32:53` **usage** — paused: 5h usage 87% ≥ 85%; sleeping until ≈17.09 10:22
- `2026-09-17 10:22:01` **usage** — resumed: 5h ? (reset ?) · 7d 23%
- `2026-09-17 10:22:37` **p02-implement** — done (5m, $2.13): Phase 2 complete. All T1-T17 (models/migrations/admin/factories/i18n/CSS) were already done by the prior session; this session finished T18 (docs.PROGRESS.md phase-2 entry, DECISIONS.md p02-implement bullets) and T19 (full gate). Gate run found and fixed real issues: ~40 ruff E501/DJ001/DJ012 findi…
- `2026-09-17 10:22:42` **p02-tests** — pass: exit 0: ................................................... [100%]
- `2026-09-17 10:29:41` **p02-review1** — changes_requested (6m, $3.36): Phase 2 delivers every spec §4 table with good constraints, indexes, factories, admin classification and i18n discipline, and I independently confirmed the whole gate: pytest (247 passed), ruff, ruff format, mypy, makemigrations --check, manage.py check, and migrate from an empty SQLite file both f…
- `2026-09-17 10:35:02` **p02-review_fix1** — done (5m, $1.75): Fixed both majors and all minors from REVIEW-r1. Fixed: - [MAJOR] Admin N+1 on pull_request→repository: added `pull_request__repository` to `list_select_related` on 8 admins (policy.PolicyViolationAdmin, churn.ChurnResultAdmin, ai_detection.AISignalAdmin, activity.{PullRequestCommitAdmin,PRFileAdmi…
- `2026-09-17 10:35:07` **p02-tests** — pass: exit 0: ................................................................ [100%]
- `2026-09-17 10:39:02` **p02-review2** — changes_requested (3m, $2.4): Round-2 verification: every r1 blocker/major and all three r1 minors are really fixed, each with a test that can fail (the admin N+1 select_related plus a 3-vs-12-row query-count test; AppSetting descriptions no longer persisted and rendered from the lazy registry instead; migration decoupled from …
- `2026-09-17 10:42:34` **p02-review_fix2** — done (3m, $0.84): Applied all r2 findings. Fixed: - [MAJOR] Added `raw` JSONField to `activity.Commit`, `activity.Review`, `activity.ReviewComment` (each is a first-class GitHub node with a unique `github_id`, matching Organization/Repository/PullRequest). Hand-edited `apps/activity/migrations/0001_initial.py` in pl…
- `2026-09-17 10:42:40` **p02-tests** — pass: exit 0: ...................................................................... [100%]
- `2026-09-17 10:45:08` **p02-review_audit2** — approve (2m, $0.98): All three r2 findings (one major, two minors) are fixed in the product, each with a test that fails without the fix. I re-ran the whole gate myself: pytest passed (286), ruff, ruff format --check, mypy, makemigrations --check --dry-run, manage.py check, plus migrate from an empty SQLite file — `raw…
- `2026-09-17 10:46:37` **p02-docs** — done (1m, $0.68): Verified phase-2 docs against actual code (models, migrations, admin, permissions). Most already true: CLAUDE.md, docs/CONFIGURATION.md (AppSetting table), docs/PROGRESS.md (phase-2 entry), docs/dev/adr/0002 (admin holds catalog.manage_settings — already correct), CHANGELOG.md (no entry, correct pe…

---
Docs: `ARCHITECTURE.md` · `RISKS.md` · `ROADMAP.md` · `DECISIONS.md` · `phases/*/PLAN.md` · `phases/*/REVIEW-r*.md` · `HANDOFF.md` (written at the end) · project docs in `docs/dev/` and `docs/user/` · raw session logs in `logs/`. Stop gracefully: `touch .autodev/STOP`.
