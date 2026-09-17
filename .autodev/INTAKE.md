# Intake — PR Radar

- **Date:** 2026-09-17
- **Spec:** docs/SPEC.md
- **Profile:** django-htmx

## Answers
- **Phase merging:** Keep `--merge-phases`. Each phase merges into `main` automatically after its tests pass, unreviewed. Stacked draft PRs stay open per phase for reading afterwards. The developer was told plainly that the run writes to `main`.
- **Scope for tonight:** All phases 0-10 from spec section 14. Run until the spec is done or the ceiling stops it.
- **CSS:** Tailwind standalone CLI (binary, no node build for the app; compiled CSS committed). Not Pico.css. Chosen for the dense tables / KPI cards and the `data-theme` token system in section 10.5.
- **GitHub credentials:** None available locally. No `GITHUB_TOKEN` in `.env`, no live GitHub calls at any point. All GitHub behaviour is exercised against JSON fixtures mocked with `respx`, per section 12. The `bootstrap_connection` path is still implemented and tested, just never run against the real API.

## Defaults the developer accepted
- Test command `uv run pytest -q`, because the spec names `pytest` + `pytest-django` and `uv` as the package manager. The full per-phase gate is `ruff check`, `ruff format --check`, `mypy` (services/ and metrics/), `pytest` — spec section 12.
- Run ceiling `--max-hours 10`, because the developer chose "all phases" from an option that recommended pairing it with a ceiling rather than grinding through the next usage window.
- e2e up/down commands left unset, because the project does not exist yet. The architect step works them out and writes them down; `manage.py runserver` on a non-default port is the expected shape.
- Everything in spec section 15 ("Рішення за замовчуванням") stands as written, unmodified.

## Left open on purpose
- e2e surface commands — architect step decides and records them in `.autodev/PROFILE.md`.
- Chart.js vendoring mechanics, exact Tailwind build invocation, file layout inside the apps named in section 3 — the run decides and logs in `docs/DECISIONS.md`.
- Anything section 15 already covers is not a question; the run applies it directly.
