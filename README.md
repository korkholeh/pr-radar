# PR Radar

A local Django tool for team leads. It pulls pull-request data from GitHub via the GraphQL API into SQLite and
shows dashboards on AI adoption / AI-policy compliance and on delivery quality and dynamics at the global,
project, repository and person level.

The developers whose pull requests are measured are not users: they have no account and are never notified.

## Read-only on GitHub

PR Radar never writes to GitHub. It has no feature that comments, labels, approves, merges, opens or closes
anything, and none is planned — the tool reads pull-request data and nothing else.

- **GraphQL.** Every operation in `apps/github_sync/queries.py` is a `query`. The codebase contains no
  `mutation`, and the only `POST` the client makes is the one GitHub's GraphQL API requires for a query.
- **REST.** Used for a handful of reads only, through a `GET`-only helper.
- **Git.** Churn analysis runs `git clone --bare` and `git fetch --prune` against a local cache. There is no
  `push`, and the token reaches `git` through `GIT_ASKPASS` and the environment, never in a remote URL, a log or
  a process argument.
- **Token scopes.** A fine-grained personal access token with read-only **Pull requests**, **Contents** and
  **Metadata** is enough, and is what `docs/GITHUB_CONNECTIONS.md` recommends. A classic token carrying the full
  `repo` scope also grants write access that PR Radar never uses, so connection verification flags it
  (`CLASSIC_PAT_WRITE_SCOPE`) and marks the connection `degraded` until you narrow it.

## Run it

Python 3.12 and [`uv`](https://docs.astral.sh/uv/), from the repository root:

```bash
uv sync
cp .env.example .env    # edit SECRET_KEY at minimum
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver 8000     # web, in one terminal
uv run python manage.py run_huey           # background worker, in another
```

Open <http://127.0.0.1:8000/>. To see the dashboards with data before connecting anything real, run
`uv run python manage.py seed_demo`.

`docs/SETUP.md` covers scheduling, backups and the rest.

## Where to read next

| | |
|---|---|
| **Using it** | [`docs/user/README.md`](docs/user/README.md) — the guide index, in the order a new user needs it |
| Finding a page fast | [`docs/user/index.md`](docs/user/index.md) — task → page map |
| Something looks wrong | [`docs/user/troubleshooting.md`](docs/user/troubleshooting.md) |
| Installing and operating | `docs/SETUP.md`, `docs/CONFIGURATION.md`, `docs/GITHUB_CONNECTIONS.md` |
| **Working on the code** | [`docs/dev/architecture.md`](docs/dev/architecture.md), then `docs/dev/adr/` and `CLAUDE.md` |
| What a metric or rule means | `docs/METRICS.md`, `docs/POLICY.md` |
| The product specification | `docs/SPEC.md` (Ukrainian, the author's source of truth) |
| What changed | `CHANGELOG.md` |

## Developing

```bash
uv run pytest -q                                            # unit and integration suite
uv run ruff check . && uv run ruff format --check . && uv run mypy   # part of the lint gate
make e2e-up && uv run pytest e2e -q; make e2e-down          # browser suite on port 8100
```

`CLAUDE.md` is the short index of commands and conventions; the full lint gate is listed there.

No test, and no part of this codebase, has ever made a live GitHub call — every GitHub behaviour is exercised
against JSON fixtures under `tests/fixtures/github/`, and the suite fails on any unmocked outbound request.
