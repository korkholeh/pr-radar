# PR Radar

A local Django tool for team leads. It pulls pull-request data from GitHub via the GraphQL API into SQLite and
shows dashboards on AI adoption / AI-policy compliance and on delivery quality and dynamics at the global,
project, repository and person level.

See `docs/SETUP.md` to install and run it, `docs/CONFIGURATION.md` for every setting, `docs/user/getting-started.md`
for how a lead uses it, and `docs/SPEC.md` (Ukrainian) for the product specification. User-visible changes are in
`CHANGELOG.md`.

For a lead using the app day to day, [`docs/user/index.md`](docs/user/index.md) maps what you're trying to do to
the page that does it, and [`docs/user/troubleshooting.md`](docs/user/troubleshooting.md) covers the handful of
things that look like a bug but aren't (an empty dashboard, a greyed small-sample number, a stale "Data as of"
banner, a failed sync, a missing churn number).
