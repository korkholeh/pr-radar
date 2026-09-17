# Review — phase 3 round 1

**Verdict:** changes_requested

Phase 3 lands a large, mostly well-built slice: token encryption with a grep-enforced single decrypt accessor, a fixture-only GraphQL client with real nested pagination and Retry-After handling, per-connection rate budgets with round-robin yielding, idempotent upserts, a thorough leak test (introspection walk + real on-disk SQLite dump + log file + error_log + rendered pages), full docs and Ukrainian parity. The project gate passes as claimed (ruff, format, mypy, makemigrations --check, manage.py check, pytest -q all green; I re-ran it). Acceptance criteria 1, 3, 4, 5, 6, 7 are genuinely proven by tests; criterion 8 is proven by test_errors.py and test_mappers.py (though the test_client.py test the plan cites for it is a tautology), and criterion 2 is proven for the main tables. All 24 PLAN tasks are checked with no unjustified [~] and no unproven environment claims. The problems are concentrated in error paths and the discovery UI: the orchestrator leaves a permanent status=running SyncRun in two separate cases (lock contention, and any repository-level error that is not 401/SSO), which makes the Sync page poll forever and contradicts PLAN T16's claimed behaviour that has no test; the discovery bulk-add silently rebinds a repository bound to another connection with no confirm and no audit entry, bypassing this phase's own documented confirm-step decision, and the rebind form is nested inside the discovery form so its button is dead in a real browser; last_synced_at advances past PRs that errored, creating exactly the permanent gap .autodev/DECISIONS.md line 18 says the design avoids; two admin GitHub-calling views return 500 rather than a visible fragment; and the T15 pipeline test does not exercise production code while the hook-containment half of T15 is unimplemented. Seven major findings, no blockers.

## [MAJOR] SyncRun row is created before the lock, so contention leaves a permanent "running" run
`apps/github_sync/services.py`

run_sync() does `run = SyncRun.objects.create(status=RUNNING)` (line 212) and only then `_acquire_lock(run)` (line 213). When the lock is held, SyncAlreadyRunning propagates and the just-created SyncRun row stays status=running with finished_at=None forever. apps/github_sync/views.py:15 picks the first running run out of the latest 20, and partials/status.html:5 attaches `hx-trigger="every 2s"` whenever `running` is truthy — so after one double-clicked Sync button (tasks.py swallows SyncAlreadyRunning and logs it) the page shows "Sync in progress" and polls every two seconds indefinitely, and manage.py sync's clear error message is accompanied by an invisible phantom run. test_sync.py::test_second_concurrent_run_raises_sync_already_running asserts only the exception, not the row count, so nothing catches this.

**Fix:** Acquire the lock first and create the SyncRun only after it succeeds, or wrap the acquisition so that SyncAlreadyRunning deletes the row (or sets status=failed with an error_log line) before re-raising. Extend test_second_concurrent_run_raises_sync_already_running to assert SyncRun.objects.filter(status=RUNNING).count() == 1 afterwards.

## [MAJOR] Repository-level errors other than 401/SSO abort the whole run and leave it status=running
`apps/github_sync/services.py`

process_one() (lines 259-287) catches only GitHubAuthError and GitHubSSOError. Everything else raised out of sync_repository — GitHubServerError after retries, SecondaryRateLimitError, RateBudgetExhausted, a GitHubSchemaError from the repository-level pullRequests page (raised by the for-loop iterator, outside the per-PR try), an IntegrityError, or a raising process_pull_request hook — propagates out of the round-robin loop and out of run_sync. The `finally` releases the lock but never touches `run`, so the run stays status=running with empty stats and an empty error_log: the UI polls it forever (same surface as the finding above) and the operator gets no record of what failed. PLAN.md T16 states "a repository error is recorded and the run continues with status=partial" and the acceptance table cites test_sync.py for it, but no such test exists — test_lock_is_released_after_a_crashing_run only asserts the lock is gone.

**Fix:** Add `except GitHubError as exc` (and a broad `except Exception`) in process_one that appends mask_secrets(...) to error_lines, increments stats["errors"] and conn_stats["errors"], and leaves the remaining queue intact; and in run_sync's finally/except path always write a terminal status (failed) plus the masked error_log before re-raising. Add the missing test: a repository whose PR list page returns 502 six times is recorded and the run finishes status=partial with another repository synced.

## [MAJOR] Discovery bulk-add silently rebinds a repository bound to another connection, with no confirm and no audit entry
`apps/catalog/services.py`

create_repositories_from_discovery() calls Repository.objects.update_or_create(github_id=node["id"], defaults={... "connection": connection ...}) (around line 115). apps/connections/views.py::_discovery_rows marks rows bound to a different connection via `bound_to`, but partials/discovery_content.html:66 renders an ordinary enabled `<input type="checkbox" name="repo">` for those rows too. Ticking a "Bound to X" row and pressing "Add selected repositories" moves Repository.connection to the new connection with no confirm=1 checkbox and no repository.rebind AuditEntry — directly bypassing the explicit-confirm design recorded as a p03-implement/decision in .autodev/DECISIONS.md. Separately, `sync_since` is in defaults, so any re-submission of an already-added repository resets sync_since to today - BACKFILL_DAYS, discarding a narrowed window and forcing a needless re-backfill. Neither behaviour has a test.

**Fix:** Skip (or refuse) nodes whose github_id already belongs to another connection and surface them as "use Rebind"; disable the checkbox for bound_to rows in the template. Move sync_since out of `defaults` into `create_defaults=` (Django 5.0+) so it is only set on creation. Add tests: bulk-adding a repo bound elsewhere does not move it, and re-submitting an existing repo leaves sync_since untouched.

## [MAJOR] The rebind form is nested inside the discovery form, so the "Rebind here" button is dead in a browser
`apps/connections/templates/connections/partials/discovery_content.html`

The per-row rebind `<form method="post" action="{% url 'connections:rebind' ... %}">` at line 75 sits inside the outer `<form method="post" action="{% url 'connections:discover' %}" id="discovery-form">` opened at line 35. Nested forms are invalid HTML; the parser drops the inner form start tag and re-parents its children, so clicking "Rebind here" submits the outer discovery POST (adding selected repositories) instead of the rebind endpoint, with the extra `connection` hidden input and `confirm` checkbox appended to it. The rebind path passes only because test_discovery.py::test_rebind_moves_connection_keeps_prs_and_writes_audit_entry POSTs the URL directly, and PLAN T23 deliberately authored no e2e rebind case — so no test drives the actual markup. docs/user/connect-github.md tells the admin to rebind from this page.

**Fix:** Take the rebind controls out of the outer form: either render the rebind button with hx-post/hx-include (htmx is already a dependency and the other action buttons use it), or give the button HTML5 `form="rebind-<pk>"` and place the `<form id="rebind-<pk>">` element outside the discovery form. Add a browser-level e2e or an assertion that the rendered page contains no nested form.

## [MAJOR] last_synced_at advances past pull requests that failed to sync, creating a permanent gap
`apps/github_sync/services.py`

sync_repository catches per-PR GitHubError, records it and continues (lines 184-187), then unconditionally sets repository.last_synced_at = run.started_at (line 197). test_sync.py::test_pr_schema_error_is_recorded_and_repository_continues:209 explicitly asserts that advance. Because the next incremental watermark is last_synced_at - SYNC_OVERLAP_MINUTES (60) and the PR loop breaks as soon as updatedAt < watermark, a PR that errored is below the watermark on every subsequent run and is never retried unless GitHub updates it again — it is silently missing from every metric. .autodev/DECISIONS.md line 18 (architect/assumption) chose repository-level watermarking specifically because a per-page watermark "trades a cheap re-read for a permanent gap when a page fails"; this is that gap, and neither PLAN.md nor DECISIONS.md justifies the deviation.

**Fix:** Track the oldest updatedAt among failed PRs in sync_repository and clamp repository.last_synced_at to just before it (or leave last_synced_at untouched when stats["errors"] for that repository is non-zero). Add a test: a repository with two PRs where the older one fails re-fetches that PR on the next incremental sync.

## [MAJOR] connection_check and repository_discover return 500 instead of a visible error fragment on any recoverable GitHub error
`apps/connections/views.py`

connection_check (line 112) calls verify_connection() and repository_discover (line 198) calls _discover_nodes() with no try/except. verify_connection handles GitHubAuthError and GitHubSSOError internally, but GitHubServerError (502 after retries), SecondaryRateLimitError, GitHubError, httpx.TransportError (network down) and ConnectionNotUsableError (inactive or token-less connection — reachable because the list view offers Check on inactive rows) all escape to a 500. Both buttons are htmx swaps, so the admin sees nothing at all. This violates CLAUDE.md's "Errors return a visible fragment, never an empty 400 body". test_views.py::test_invalid_token_check_renders_visible_error_not_empty_400 covers only 401, which never reaches the view.

**Fix:** Wrap both call sites in `try/except (GitHubError, ConnectionNotUsableError) as exc` and render the row/discovery fragment with a translated `role="alert"` message (a code+params message, per the check_codes pattern, not a raw str(exc)). Add tests for a 502 on Check and on Discover asserting 200 plus visible alert text.

## [MAJOR] T15's hook test does not exercise production code, and the hook-containment half of T15 is unimplemented
`apps/github_sync/tests/test_pipeline.py`

test_hook_runs_once_per_pr_on_commit (line 21) never calls sync_repository or run_sync — it opens its own transaction.atomic() and registers its own `transaction.on_commit(lambda pk=...: pipeline.process_pull_request(pk))`, so it proves that Django's on_commit works, not that the orchestrator calls the hook once per synced PR. It also monkeypatches pipeline.process_pull_request, which could not intercept production anyway: services.py imports the function at module level (line 26) and passes the bound object to partial(), so services.process_pull_request keeps pointing at the real function. Separately, PLAN T15 promises "a raising hook does not roll back the PR's rows but is recorded in error_log" — nothing implements that (a raising on_commit callback fires as sync_repository's atomic block exits and, not being a GitHubError, escapes to abort the entire run per the finding above), and the second test asserts the unrelated rollback case instead.

**Fix:** Rewrite the test to run run_sync() against the existing fixture sequence with apps.github_sync.services.process_pull_request monkeypatched, asserting one call per synced PR with the right pk. Then implement the containment: catch exceptions from the hook (inside the on_commit callback or via a wrapper), append a masked line to the run's error_log, and add a test that the PR's rows survive and the run reports the error.

## [MINOR] test_missing_author_login_raises_schema_error is a tautology and cannot prove acceptance criterion 8
`apps/github_sync/tests/test_client.py`

The test (line 188) fetches the fixture through the client, then calls require(data, "repository.pullRequests.nodes[0].author.login") itself and asserts the path it just supplied comes back on the exception — it is the same assertion as test_errors.py, run on a field the client explicitly does NOT depend on (mappers.map_pull_request reads author.login via optional(), and test_mappers.py::test_ghost_author_maps_to_none_not_a_failure asserts that a missing author must not fail). PLAN.md's acceptance table cites this test for criterion 8 ("a field the client depends on"); the criterion is in fact proven only by test_errors.py and test_mappers.py::test_missing_required_field_raises_with_path.

**Fix:** Point the test at a field the client really depends on — e.g. a pull_requests fixture missing `pageInfo.endCursor` while hasNextPage is true, or missing a PR node `id` — driven through client.paginate()/the mapper so the production code raises, and assert exc.path. Fix PLAN.md's acceptance table accordingly.

## [MINOR] PERM_CONTENTS renders "was not checked" even when the check succeeded or failed
`apps/connections/check_codes.py`

verify_connection emits PERM_CONTENTS with outcome ok, fail or unavailable (services.py lines 124-132), but CHECK_CODES has a single message for the code: "Repository contents access was not checked." (line 35). partials/form.html:54 prints that message for every outcome, so an admin whose contents permission is fine — or is genuinely missing — reads "was not checked", and the hint ("Add a repository to this connection, then re-check") is shown for a real failure. docs/GITHUB_CONNECTIONS.md presents these strings as the operator's diagnosis.

**Fix:** Split into three codes (PERM_CONTENTS_OK / PERM_CONTENTS_DENIED / PERM_CONTENTS_UNAVAILABLE) or key the message on outcome, and extend test_verify.py::test_every_emitted_code_has_a_check_codes_entry to assert the rendered message matches the outcome. The docs test will then force the new codes to be documented.

## [MINOR] _release_lock deletes the global lock by name, not by owner
`apps/github_sync/services.py`

_release_lock() (line 73) runs SyncLock.objects.filter(name=LOCK_NAME).delete(). If run A's lock went stale and run B stole it (_acquire_lock line 65), run A finishing later deletes B's lock row while B is still syncing, after which a third run can start concurrently — the exact condition SyncLock exists to prevent. SYNC_LOCK_STALE_MINUTES is 360, so this needs a >6h run, but that is plausible for a first backfill.

**Fix:** Release with SyncLock.objects.filter(name=LOCK_NAME, sync_run=run).delete(), and have _acquire_lock return the lock row so run_sync's finally can target it by pk.

## [MINOR] Raw dict indexing bypasses the GitHubSchemaError contract in three places
`apps/github_sync/client.py`

client.graphql reads rate_limit["remaining"] and rate_limit["resetAt"] directly (lines 72-73); apps/connections/views.py::_discovery_rows reads node["isArchived"], node["id"], node["owner"]["login"] (lines 180-190); apps/catalog/services.py::create_repositories_from_discovery reads owner["id"], owner["login"], node["id"], node["name"], node["nameWithOwner"], node["isPrivate"], node["isArchived"]. Schema drift in any of these raises a bare KeyError rather than GitHubSchemaError naming the JSON path — the whole point of errors.require() per the module docstrings and acceptance criterion 8, and on the discovery views it surfaces as a 500.

**Fix:** Route these through errors.require()/optional() like the mappers do.

## [MINOR] Any logged-in lead can trigger a global sync, while the user docs say syncing is admin-only
`apps/github_sync/views.py`

sync_page, sync_run and sync_status carry only @login_required (lines 19-36), and nav.html shows "Sync" to every authenticated user. POST /sync/run/ is a state-changing, outbound-traffic operation against every connection's rate budget. PLAN.md deliberately left /sync/ ungated because it shows no per-project data, which is a defensible read for the page — but not obviously for the run button, and docs/user/connect-github.md tells the reader "Connecting and syncing is admin-only". test_views.py only ever logs in as lead_user, so no test pins the intent either way.

**Fix:** Either add @permission_required("catalog.manage_settings", raise_exception=True) to sync_run (keeping the page and status readable by leads) with a lead-refusal test, or correct the sentence in docs/user/connect-github.md. Record whichever you pick in .autodev/DECISIONS.md.

## [NIT] Stale docstrings: the "exactly one query" claim, an unused logger, and a log that never happens
`apps/connections/context_processors.py`

The module docstring says "an admin's total cost here is exactly the one connections query", but the uncached get_int("TOKEN_EXPIRY_WARNING_DAYS") makes it two on every admin page render — correctly documented in .autodev/DECISIONS.md and asserted as 2 by the test, so only this docstring (and PLAN.md's acceptance row citing a non-existent test_banner_costs_one_query) is out of date. Likewise apps/github_sync/services.py defines a module `logger` that is never used, and apps/github_sync/rate_limit.py's docstring plus PLAN T6 say a budget wait "logs it" while nothing logs anything.

**Fix:** Correct the context-processor docstring and the PLAN acceptance row to the real test name; either drop the unused loggers or add the intended INFO log line for a rate-limit wait (which would also give the leak test's log-file assertion something from the sync path to bite on).
