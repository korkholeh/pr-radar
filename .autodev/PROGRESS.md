# Autodev progress — pr-radar

- **Status:** running
- **Current:** roadmap
- **Spec:** `docs/SPEC.md` · **Branch:** `autodev/spec-20260917-0714`
- **Stack:** Python 3.12 + Django 5.2 LTS + SQLite (WAL, ORM-only for Postgres portability) + httpx/GraphQL + huey/SqliteHuey + Django templates/htmx/Alpine + Tailwind standalone CLI (committed CSS) + vendored Chart.js + django-tables2/django-filter + XlsxWriter, tested with pytest/pytest-django/factory_boy/freezegun/respx, linted with ruff + mypy, managed by uv. · **Profile:** `django-htmx`
- **Test command:** `uv run pytest -q` · **E2E:** `uv run pytest e2e -q`
- **Usage:** 5h 15% (reset 17.09 10:20) · 7d 20%
- **Totals:** 1 sessions · 0.2 h agent time · ≈$3.16 API-equivalent
- **Clock:** 0.2 h since the run was created · 0.2 h working · 0.0 h paused on the usage limit · 0.0 h not running
- **Updated:** 2026-09-17 07:26:52

## Timeline

- `2026-09-17 07:26:52` **architect** — done (12m, $3.16): PR Radar is one Django 5.2 project over a single SQLite file, with one web process and one huey worker — no broker, no second service, no SPA. Ten apps per spec §3 sit behind three deliberate choke points: a `GitHubAuth` protocol so no code but `connections` ever holds a token, `scope_for_user()` s…

---
Docs: `ARCHITECTURE.md` · `RISKS.md` · `ROADMAP.md` · `DECISIONS.md` · `phases/*/PLAN.md` · `phases/*/REVIEW-r*.md` · `HANDOFF.md` (written at the end) · project docs in `docs/dev/` and `docs/user/` · raw session logs in `logs/`. Stop gracefully: `touch .autodev/STOP`.
