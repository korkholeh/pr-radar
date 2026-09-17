# Autodev progress — PR Radar

- **Status:** running
- **Current:** phase 1/11 · step `plan`
- **Spec:** `docs/SPEC.md` · **Branch:** `autodev/spec-20260917-0714` · **PR:** https://github.com/korkholeh/pr-radar/pull/1
- **Stack:** Python 3.12 + Django 5.2 LTS + SQLite (WAL, ORM-only for Postgres portability) + httpx/GraphQL + huey/SqliteHuey + Django templates/htmx/Alpine + Tailwind standalone CLI (committed CSS) + vendored Chart.js + django-tables2/django-filter + XlsxWriter, tested with pytest/pytest-django/factory_boy/freezegun/respx, linted with ruff + mypy, managed by uv. · **Profile:** `django-htmx`
- **Test command:** `uv run pytest -q` · **E2E:** `uv run pytest e2e -q`
- **Usage:** 5h 20% (reset 17.09 10:20) · 7d 20%
- **Totals:** 2 sessions · 0.3 h agent time · ≈$4.92 API-equivalent
- **Clock:** 0.3 h since the run was created · 0.3 h working · 0.0 h paused on the usage limit · 0.0 h not running
- **Updated:** 2026-09-17 07:32:09

## Phases

| # | Phase | User-facing | Status | Commit | Warnings |
|---|---|---|---|---|---|
| 1 | Skeleton, auth, i18n and design tokens | yes | ⏳ pending |  |  |
| 2 | Domain models, migrations and Django admin | no | ⏳ pending |  |  |
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

---
Docs: `ARCHITECTURE.md` · `RISKS.md` · `ROADMAP.md` · `DECISIONS.md` · `phases/*/PLAN.md` · `phases/*/REVIEW-r*.md` · `HANDOFF.md` (written at the end) · project docs in `docs/dev/` and `docs/user/` · raw session logs in `logs/`. Stop gracefully: `touch .autodev/STOP`.
