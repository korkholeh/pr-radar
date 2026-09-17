# End-to-end tests

Playwright specs that drive the real app through a browser. They never start the server themselves — the
orchestrator (or you, locally) starts it first.

## Running

```bash
make e2e-up            # migrate, seed, collectstatic, compilemessages, start worker + server on :8100
uv run pytest e2e -q   # attaches to the running surface
make e2e-down          # kill the worker + server, remove .e2e/
```

`e2e-up` blocks until the server answers on port 8100 (or fails after 45s). If a spec can't reach the surface it
fails immediately, naming the exact command to run.

## Surfaces

`e2e/surfaces.toml` names the one surface these specs drive (`app`, the web UI). Its address comes from the
`E2E_BASE_URL` env var, defaulting to `http://127.0.0.1:8100` — the same port `make e2e-up` binds. There is no
per-developer override file yet; add `surfaces.local.toml` (gitignored) if you ever need one.

## Personas

`e2e/support/personas.py` names `USER_ADMIN` and `USER_LEAD`, matching the accounts `manage.py seed_e2e` creates
(usernames `e2e-admin` / `e2e-lead`; passwords from `E2E_ADMIN_PASSWORD` / `E2E_LEAD_PASSWORD`, falling back to
the documented defaults). `e2e/support/auth.py::log_in` drives the real login form — no back door.

## Adding a case

1. Add the case to the relevant `e2e/plans/<feature>.plan.yaml` first — id, priority, steps, expected outcome,
   and the oracle it comes from (spec section, ADR, or the UI's own promise).
2. Write the spec under `e2e/<surface>/`, opening with the `[qa:<feature>:<case-id>]` tag.
3. Assert on what the user can observe (visible text, attribute, URL) — never on internal state.

See `.autodev/guides/e2e-authoring.md` and `.autodev/guides/qa-oracles.md` for the full conventions.

## Report

Each run should write `e2e/RESULTS.md` (gitignored) summarising pass/fail per case; `e2e/artifacts/` holds
screenshots/traces on failure (also gitignored). Neither exists until the first failing run needs them.
