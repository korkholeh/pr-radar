# Phase 4 — Identity resolution and derived PR fields

**Goal:** GitHub logins and git emails collapse into people, bots are excluded and counted separately, and
every cached PR field the metrics depend on is computed, idempotent and tested.

**User-facing:** yes — Settings → People (people list) and Settings → People → Unmapped identities (the queue).

---

## Context

### What exists

- **Rows, no interpretation.** Phase 3's sync writes `activity.{PullRequest,Commit,PullRequestCommit,PRFile,
  Review,ReviewComment,CheckStatus}` and creates `catalog.Identity` rows with
  `get_or_create(kind, value, person=None)` (`apps/github_sync/upserts.py::_identity_for`). Nothing has ever
  written a `Person`, and no derived field on `PullRequest` has ever been computed.
- **Fields already on the model** (phase 2), all currently `None`/default: `size_bucket`, `is_revert`,
  `reverts_pr`, `is_hotfix`, `has_test_changes`, `is_rubber_stamp`, `is_self_merged`, `review_rounds`,
  `commits_after_first_review`, `effective_additions`, `effective_deletions`, `first_commit_at`,
  `first_review_at`, `first_approval_at`, `last_activity_at`, `merge_method`; plus `PRFile.is_test` and
  `PRFile.is_excluded`.
- **`ready_for_review_at` is half-done.** `mappers.map_ready_for_review_at()` already reads
  `ReadyForReviewEvent` from `PR_TIMELINE_QUERY` and `upsert_pull_request()` stores it; it is `None` for a PR
  that was never a draft. Spec §4.2 wants `created_at` in that case — that fallback is this phase's.
- **`Identity.save()`/`clean()` normalise** through `catalog.normalize.normalize_identity_value()`
  (`strip().casefold()`); bulk writers must call it themselves.
- **Settings already seeded** (`apps/catalog/setting_defs.py` + migration `0002`): `BOT_LOGIN_SUFFIXES`
  (`["[bot]"]`), `BOT_LOGINS` (`["dependabot","renovate","github-actions"]`), `EXCLUDED_PATH_GLOBS`,
  `TEST_PATH_GLOBS`, `PR_SIZE_BUCKETS` (`{"XS":10,"S":100,"M":400,"L":1000}`), `RUBBER_STAMP_MAX_MINUTES`
  (10). Read them with `catalog.services.get_setting()/get_list()/get_int()/get_dict()`. **No new setting key
  is needed.**
- **The hook is wired and contained.** `github_sync.pipeline.process_pull_request(pull_request_id)` is empty
  and is called by `services._run_post_processing()` after the PR transaction commits; a raising hook is
  logged, masked into `error_lines` and counted in `stats["errors"]` without aborting the run
  (`apps/github_sync/tests/test_pipeline.py`).
- **UI machinery.** `config.htmx.is_htmx()`, `apps/accounts/services.record_audit()`, the
  `catalog.manage_settings` permission (admin group), `templates/partials/nav.html`, and the
  `connections` app as the worked example of list page + `partials/` fragments + POST actions.
- **Gates that bind this phase.** `tests/test_urls_login.py` walks `urlpatterns` (every new named URL must
  reverse with `SAMPLE_ARGS = {pk: 1, …}` and redirect anonymous users); `tests/test_translations.py` (no
  empty/fuzzy `msgstr`, no stale `.mo`, matching placeholders); `tests/test_css_tokens.py` (app.css freshness
  over `templates/`, `apps/`, `static/js/` excluding migrations and tests → `make css` after new templates);
  `tests/test_no_hardcoded_colors.py`; `conftest.py`'s respx guard (no outbound HTTP, ever); mypy over
  `apps/*/services.py` and `apps/*/selectors.py`.

### What this phase changes

Two apps grow behaviour and one gains a UI.

- `catalog` becomes the owner of **identity resolution**: a new `identity.py` service (auto-mapping, bot
  detection, person auto-creation, person merge), a new `selectors.py` (queue + counters, both starting from
  `scope_for_user()`'s `ScopeFilter`), a new `globs.py` path matcher shared with `activity` (and, later,
  `policy`/`churn`), plus `views.py`/`urls.py`/`forms.py`/templates for Settings → People.
- `activity` becomes the owner of **derived fields**: a new `derive.py` (pure computation + one writer) and a
  new `selectors.py` that encodes the metric-population rule (bots and `exclude_from_metrics` people out,
  counted separately).
- `github_sync` changes in two small places: `upserts.py` also materialises the *commit author email* identity
  next to the login identity (new `Commit.author_email_identity` FK, one migration) so GitHub's own
  email↔login pairing survives into post-processing; `pipeline.py` becomes
  `resolve_identities_for_pull_request()` → `derive_pull_request()`.

Nothing in `ai_detection`, `policy`, `metrics` or `churn` changes.

### Key files

```
apps/catalog/globs.py                       # NEW  glob matcher (**, *, basename patterns)
apps/catalog/identity.py                    # NEW  auto-mapping, bots, person auto-create, merge_people()
apps/catalog/selectors.py                   # NEW  unmapped queue, people list, counters (ScopeFilter-first)
apps/catalog/forms.py                       # NEW  PersonForm, AssignIdentityForm, MergePeopleForm
apps/catalog/views.py  urls.py              # NEW  Settings → People + unmapped-identity queue
apps/catalog/templates/catalog/…            # NEW  people.html, identities.html, partials/*
apps/activity/derive.py                     # NEW  compute_derived() + derive_pull_request(s)()
apps/activity/selectors.py                  # NEW  pull_requests_for_metrics(), bot_pull_request_count()
apps/activity/migrations/0002_commit_author_email_identity.py   # NEW
apps/github_sync/upserts.py                 # write author_email_identity; always create the email identity
apps/github_sync/pipeline.py                # resolve → derive
config/urls.py  templates/partials/nav.html # route + nav entry
pyproject.toml                              # mypy files += apps/catalog/identity.py, apps/activity/derive.py
docs/user/map-people.md  CHANGELOG.md  locale/uk/LC_MESSAGES/django.po  static/css/app.css
```

---

## Design

### 1. Path glob matching — `apps/catalog/globs.py`

`EXCLUDED_PATH_GLOBS` mixes two shapes: rooted patterns with separators (`**/migrations/**`) and bare
basename patterns (`*.lock`, `test_*.py`). `PurePath.match()` cannot express `**` on Python 3.12
(`full_match` is 3.13) and `fnmatch` lets `*` cross `/`. So one small, tested translator:

```python
def compile_globs(patterns: Sequence[str]) -> list[re.Pattern[str]]
def matches_any(path: str, compiled: Sequence[re.Pattern[str]]) -> bool
```

Semantics, fixed and tested: `*` matches within one segment, `?` one character, `**` any number of whole
segments (`**/x/**` also matches `x/y`), a pattern **without** `/` is matched against the basename at any
depth, a pattern with `/` is matched against the full repo-relative path. Paths are compared case-sensitively
(git is), with backslashes normalised to `/`. Invalid patterns are skipped with a warning, never raised — a
typo in a setting must not break every sync.

### 2. Identity resolution — `apps/catalog/identity.py`

The rule that decides whether an identity is "recognised": **a GitHub login identifies exactly one GitHub
account, so it is always recognised; a bare git email is not.** That is what puts emails, and only emails,
in the queue (spec §4.1) and matches the acceptance criterion ("a seeded unmatched email").

```python
def is_bot_login(login: str) -> bool                     # BOT_LOGIN_SUFFIXES + BOT_LOGINS, casefolded
def login_from_noreply_email(email: str) -> str | None   # ID+login@ / login@users.noreply.github.com
def person_for_login(login_identity: Identity) -> Person # get-or-create, is_bot from is_bot_login()
def resolve_identity(identity: Identity) -> Identity     # idempotent single-row resolution
def resolve_identities_for_pull_request(pr_id: int) -> None   # the pipeline entry point
def merge_people(source: Person, target: Person, actor: User | None) -> Person
```

`resolve_identity()`:
1. `kind == github_login` and `person is None` → create `Person(display_name=<original-case login when known,
   else the stored value>, is_bot=is_bot_login(value))`, attach. Never touches an already-mapped identity, so
   a lead's assignment always wins.
2. `kind == git_email` and `person is None` → (a) `login_from_noreply_email()` hit → `get_or_create` the
   `github_login` identity, resolve it, adopt its person; (b) otherwise, if a `Commit` row pairs this email
   identity with a login identity (`author_email_identity` ↔ `author_identity`, see §4), adopt that login's
   person; (c) otherwise leave `person = None` → it is a queue item.
3. `is_bot` is (re)asserted on an auto-created person only. A person a human has edited is never re-flagged by
   a sync — the lead's "mark bot"/"not a bot" is durable. Auto-created is recognised by "person has exactly
   one identity, created by us, and no `AuditEntry` touched it"; rather than infer that, the person carries no
   extra state and the rule is simply: **bot flags are written only at person creation**, plus explicitly by
   the UI action.

`resolve_identities_for_pull_request()` resolves the PR's author, `merged_by`, every review/review-comment
author and every commit author/committer/email identity — a bounded `select_related` walk, O(identities) per
PR, and a no-op on the second pass because every step is `person is None`-guarded.

`merge_people(source, target)` runs in one transaction: re-point **every** `Identity` of `source` to `target`,
append `source.notes` to `target.notes` when non-empty, keep `target`'s flags, `record_audit(actor,
"person.merge", target, before={"source": …, "identities": [...]}, after={"target": …})`, then delete the now
identity-free `source`. Because `PullRequest.author` points at `Identity` (never at `Person`), no PR row is
touched and none can be orphaned; the test asserts identity count, PR count and `PullRequest.author.person`
after the merge.

### 3. Derived fields — `apps/activity/derive.py`

```python
@dataclass(frozen=True)
class DerivedFields: ...                 # every field below, plus the PRFile flags
def compute_derived(pr: PullRequest) -> DerivedFields          # pure, reads prefetched rows only
def derive_pull_request(pr_id: int) -> None                    # loads, computes, saves update_fields
def derive_pull_requests(queryset: QuerySet[PullRequest]) -> int  # bulk entry for phase 7's `recompute`
```

Definitions (spec §4.2, §8.2, §15). Every one of them returns `None` rather than `0` when the input is absent.

| Field | Rule |
|---|---|
| `PRFile.is_excluded` / `is_test` | `matches_any(path, EXCLUDED_PATH_GLOBS / TEST_PATH_GLOBS)` |
| `effective_additions` / `effective_deletions` | sum over non-excluded `PRFile`; `None` when the PR has no file rows at all |
| `has_test_changes` | any non-excluded file with `is_test` |
| `size_bucket` | `effective_additions + effective_deletions` against `PR_SIZE_BUCKETS`: XS `<10`, S `<100`, M `<400`, L `<1000`, XL `≥1000`; `None` when effective lines are `None` |
| `ready_for_review_at` | the stored timeline value if set; else `None` while `is_draft`; else `created_at` |
| `first_commit_at` | min `authored_at or committed_at` over the PR's commits |
| `first_review_at` | earliest `Review.submitted_at` **or** `ReviewComment.created_at` by a non-author, non-bot identity (spec §8.2) |
| `first_approval_at` | earliest `Review.submitted_at` with `state=APPROVED` from a non-author, non-bot identity |
| `last_activity_at` | max of `created_at`, `updated_at_github`, `merged_at`, `closed_at`, last commit, last review, last comment |
| `review_rounds` | `count(CHANGES_REQUESTED reviews by a non-author) + 1` |
| `commits_after_first_review` | commits whose `committed_at > first_review_at`; `None` when there is no review |
| `is_rubber_stamp` | `size_bucket in {L, XL}` **and** `first_approval_at - ready_for_review_at < RUBBER_STAMP_MAX_MINUTES` (strict `<`; exactly 10 min is **not** a rubber stamp) **and** no `ReviewComment` and no review with a non-empty body |
| `is_self_merged` | `merged_by` resolves to the same `Person` as `author` (same `Identity` when either is unmapped) |
| `is_hotfix` | title matches `^\s*(hotfix|fix)\b`, or `head_ref` starts with `hotfix/` or `fix/`, or a label contains `hotfix` (case-insensitive) |
| `is_revert` | title/body matches `Revert "…"`, `This reverts commit <sha>`, or `Reverts <owner>/<repo>#<n>` (spec §8.2 `revert_rate`) |
| `reverts_pr` | resolved in that order: `Reverts owner/repo#N` → that repo's PR `#N`; `This reverts commit <sha>` → the `Commit` with that sha (or a PR whose `merge_commit_sha` is it) → its PR; `Revert "<title>"` → the most recent **merged** PR in the same repository with that exact title, merged before this PR was created. Unresolvable → `is_revert=True`, `reverts_pr=None`, never a guess. Self-reference is refused. |

`merge_method` is deliberately **not** derived here — see *Out of scope*.

Idempotency is structural: `compute_derived()` reads only stored rows and settings, writes nothing, and every
rule is a total function of them. `ready_for_review_at`'s fallback is written into the same field it reads,
so the second pass sees a set value and keeps it; a re-sync re-writes the timeline value and derive re-applies
the fallback to the same result. `derive_pull_request()` saves with an explicit `update_fields` list and
`PRFile` flags via `bulk_update`, and the test asserts a byte-equal field snapshot across two passes.

### 4. Sync seam — the commit email↔login pairing

Today `upserts.py` does `_identity_for_login(login) or _identity_for_email(email)`: when GitHub tells us both,
the email identity is never even created and the pairing is lost. Post-processing therefore cannot apply spec
§4.1's second auto-mapping rule ("an email GitHub linked to a commit author") unless we keep that fact.

`Commit` gains one nullable FK, `author_email_identity` (`SET_NULL`, `related_name="authored_commits_by_email"`),
written by `upsert_pull_request()` alongside `author_identity`. `author_identity` keeps its current meaning and
fallback (login, else email). This is an auxiliary field, explicitly allowed by spec §4 ("допоміжні поля агент
додає за потреби"), and it keeps identity resolution where the architecture puts it — in post-processing, not in
the writer — so `recompute` can redo the mapping without a re-sync.

### 5. Metric population — `apps/activity/selectors.py`

```python
def pull_requests_for_metrics(scope: ScopeFilter) -> QuerySet[PullRequest]
def bot_pull_request_count(scope: ScopeFilter) -> int
def unmapped_identity_count(scope: ScopeFilter) -> int     # in catalog.selectors
```

Both start from `scope_for_user()`'s `ScopeFilter` (never `objects.all()`), per CLAUDE.md's choke-point rule.
`pull_requests_for_metrics()` excludes PRs whose author's person has `is_bot` or `exclude_from_metrics`;
`bot_pull_request_count()` counts exactly the `is_bot` complement. Phase 7's `metrics.compute()` is required
to build on these; today they exist, are tested, and feed the People page's counters.

### 6. Settings → People UI (`catalog`)

Thin views, `@login_required` + `@permission_required("catalog.manage_settings", raise_exception=True)`, rules
in `identity.py`, queries in `selectors.py`, every mutation POST, every htmx endpoint returning a fragment and
the same URL returning a full page when `HX-Request` is absent.

| URL name | Path | Action |
|---|---|---|
| `catalog:people` | `/settings/people/` | people list (name, team, identities, is_bot, exclude, PR count), with the unmapped and bot counters |
| `catalog:person_create` | `/settings/people/new/` | create a person |
| `catalog:person_edit` | `/settings/people/<pk>/edit/` | display name, team, role hint, notes, `is_bot`, `exclude_from_metrics` |
| `catalog:person_merge` | `/settings/people/merge/` | merge two people (source/target selects, confirmation copy naming both) |
| `catalog:identity_queue` | `/settings/people/identities/` | the unmapped-identity queue, paginated |
| `catalog:identity_assign` | `/settings/people/identities/<pk>/assign/` | assign to an existing person |
| `catalog:identity_create_person` | `/settings/people/identities/<pk>/create-person/` | create a person from the identity |
| `catalog:identity_mark_bot` | `/settings/people/identities/<pk>/mark-bot/` | create/flag a bot person and attach |
| `catalog:identity_exclude` | `/settings/people/identities/<pk>/exclude/` | attach and set `exclude_from_metrics` |

Every mutation writes an `AuditEntry` (`identity.assign`, `person.create`, `person.update`, `person.merge`,
`identity.mark_bot`, `identity.exclude`) — spec's "hours of manual work" data (ARCHITECTURE *What must never be
lost* #3). Errors render a visible fragment with `role="alert"`, never an empty 400. `Person.notes` appears on
the person form only and nowhere else. Every new string gets its Ukrainian translation in this phase; the
counters use `ngettext`, never concatenation.

### Architecture conformance

Follows `.autodev/ARCHITECTURE.md` as written: no new app (identity resolution is a service over `catalog`
models, exactly as the "Apps I considered and did not create" note prescribes); the post-processing contract
`identity.resolve → activity.derive → …` is filled in order inside the existing hook; selectors take a
`ScopeFilter`; derived fields stay system-owned and fully recomputable; `Person`/`Identity` mapping stays
operator-owned and is never overwritten by a sync. **One deviation** — a new auxiliary column
`Commit.author_email_identity` (§4), justified above and logged in DECISIONS.md.

---

## Tasks

- [x] **T1: Glob matcher.** `apps/catalog/globs.py` (`compile_globs`, `matches_any`). Tests:
      `apps/catalog/tests/test_globs.py` — `**/migrations/**` matches `app/migrations/0001.py` and
      `migrations/0001.py`; `*.lock` matches `sub/dir/uv.lock` but not `lockfile.py`; `tests/**` matches only
      from the root; `test_*.py` matches at any depth; `*` does not cross `/`; an invalid pattern is skipped,
      not raised; empty pattern list matches nothing.
- [x] **T2: Bot detection + noreply parsing.** `apps/catalog/identity.py`: `is_bot_login()`,
      `login_from_noreply_email()`. Tests: `apps/catalog/tests/test_identity.py` — `dependabot[bot]` (suffix),
      `renovate` (exact), `Github-Actions` (case), `dependabot-preview` (not in the list → not a bot),
      `12345+octocat@users.noreply.github.com` → `octocat`, `octocat@users.noreply.github.com` → `octocat`,
      `octocat@example.com` → `None`, `+@users.noreply.github.com` → `None`.
- [x] **T3: `Commit.author_email_identity`.** Model field + migration
      `apps/activity/migrations/0002_commit_author_email_identity.py`; `upserts.py` creates the email identity
      even when a login exists and writes both FKs. Tests: extend
      `apps/github_sync/tests/test_upserts.py` — a commit with `author.user.login` **and** `author.email`
      creates two identities and sets both FKs; `author_identity` still falls back to the email identity when
      there is no login; a second upsert creates no extra identity row.
- [x] **T4: `resolve_identity` + per-PR resolution.** `identity.py`: `person_for_login()`,
      `resolve_identity()`, `resolve_identities_for_pull_request()`. Tests in `test_identity.py` — a new login
      identity auto-creates a person; a bot login auto-creates `is_bot=True`; a noreply email adopts the
      login's person; a GitHub-linked commit email adopts the login's person; a bare email stays `person=None`;
      an already-mapped identity is never re-pointed (lead's assignment wins); resolving twice creates no
      second person and no second identity.
- [x] **T5: `merge_people`.** `identity.py::merge_people()` + `AuditEntry`. Tests:
      `apps/catalog/tests/test_person_merge.py` — every identity of the source re-points to the target, the
      source row is gone, identity count and PR count are unchanged, no identity has `person=None` afterwards,
      every PR still resolves to a person, notes are preserved, an audit entry is written, merging a person
      into itself is refused with a `ValidationError`.
- [x] **T6: Derive — files, size, tests.** `apps/activity/derive.py` with `PRFile.is_excluded/is_test`,
      `effective_additions/deletions`, `has_test_changes`, `size_bucket`. Tests:
      `apps/activity/tests/test_derive.py` — a normal PR; an excluded-path-only PR with effective lines `== 0`
      and `size_bucket == "XS"`; a PR with no file rows → both effective fields `None` and `size_bucket
      None`; bucket boundaries at exactly 10 / 100 / 400 / 1000 lines.
- [x] **T7: Derive — timestamps.** `ready_for_review_at` fallback, `first_commit_at`, `first_review_at`,
      `first_approval_at`, `last_activity_at`. Tests in `test_derive.py` — a never-drafted PR gets
      `ready_for_review_at == created_at`; a drafted-then-readied PR keeps the timeline value; a still-draft PR
      keeps `None`; a self-review and a bot review are ignored for `first_review_at`; a review comment earlier
      than the first review wins; a PR with no reviews has `first_review_at is None` (not `created_at`).
- [x] **T8: Derive — review shape.** `review_rounds`, `commits_after_first_review`, `is_rubber_stamp`,
      `is_self_merged`. Tests in `test_derive.py` — no reviews → `review_rounds == 1`; two CHANGES_REQUESTED →
      `3`; commits before/after the first review are split correctly; **a rubber stamp at exactly the
      10-minute boundary is not one** (and at 9m59s it is); an L-size PR with a review comment is not a rubber
      stamp; an M-size fast approval is not one; self-merge detected via two identities of the same person, and
      not flagged when `merged_by` is someone else.
- [x] **T9: Derive — revert and hotfix.** `is_revert`, `reverts_pr`, `is_hotfix`. Tests in `test_derive.py` —
      the revert chain: PR #1 merged, PR #2 titled `Revert "…"` with `This reverts commit <sha>` resolves
      `reverts_pr == #1` and `#1.reverted_by` contains `#2`; `Reverts owner/repo#1` resolves by number;
      an unresolvable revert sets `is_revert=True, reverts_pr=None`; a PR cannot revert itself; `hotfix/…`
      branch, `Fix: …` title and a `hotfix` label each set `is_hotfix`, `prefix-fix` does not.
- [x] **T10: Derive writer + idempotency.** `derive_pull_request()`, `derive_pull_requests()`; explicit
      `update_fields`, `bulk_update` for `PRFile`. Tests in `test_derive.py` —
      `test_second_derive_pass_changes_nothing` snapshots every derived field (PR + `PRFile` flags) and
      asserts equality after a second call; `test_derive_query_count` bounds the queries per PR.
- [x] **T11: Metric population selectors.** `apps/activity/selectors.py` (`pull_requests_for_metrics`,
      `bot_pull_request_count`) and `apps/catalog/selectors.py` (`unmapped_identities`, `people_for_settings`,
      `unmapped_identity_count`), all `ScopeFilter`-first. Tests:
      `apps/activity/tests/test_selectors.py` — a bot-authored PR is absent from
      `pull_requests_for_metrics()` and present in `bot_pull_request_count()`; an `exclude_from_metrics`
      person's PR is excluded but **not** counted as a bot; a PR with an unmapped author is still in the
      metric population; `test_selectors.py` in catalog for the queue query + `assertNumQueries`.
- [x] **T12: Wire the pipeline.** `pipeline.process_pull_request()` = `resolve_identities_for_pull_request()`
      → `derive_pull_request()`. Tests: extend `apps/github_sync/tests/test_pipeline.py` — after a fixture
      sync, the PR has a person-mapped author and non-null derived fields; a failure inside derive is still
      contained by `_run_post_processing()` (existing containment test extended); a second full sync leaves
      every derived field identical.
- [x] **T13: People UI — list and person CRUD.** `apps/catalog/{views,forms,urls}.py`, `templates/catalog/
      people.html` + `partials/`, `config/urls.py`, nav entry. Tests:
      `apps/catalog/tests/test_views_people.py` — admin 200, lead 403 on the same URL, anonymous redirect (via
      the global URL walk), create/edit persists and writes an `AuditEntry`, `HX-Request` returns the fragment
      and a normal request the full page, an invalid form renders a visible `role="alert"` fragment,
      `assertNumQueries` on the list.
      **Not started yet — no `forms.py`/`views.py`/`urls.py`/templates exist for `catalog` in this session.**
      `apps/catalog/selectors.py::people_for_settings()` already annotates `pr_count` (via
      `Count("identities__authored_pull_requests", distinct=True)`) for T13's list column, alongside
      `prefetch_related("identities")` — reuse it rather than re-annotating in the view. Planned shape (not yet
      written): `PersonForm` (ModelForm: `display_name, team, role_hint, notes, is_bot, exclude_from_metrics`),
      `AssignIdentityForm`/`MergePeopleForm` (plain forms with a `Person` `ModelChoiceField`;
      `MergePeopleForm.clean()` should refuse `source == target` as a visible form error, on top of
      `merge_people()`'s own `ValidationError` backstop), views modelled on `apps/connections/views.py`'s
      `is_htmx()` + `partials/` pattern, URLs exactly as listed in the Design section above (all reversible
      with `SAMPLE_ARGS = {"pk": 1}`).
- [x] **T14: People UI — the unmapped queue and its actions.** `identity_queue` + assign / create person /
      mark bot / exclude, all POST. Tests in `test_views_people.py` — a seeded unmatched email appears in the
      queue and **the rendered row count equals the model count**; assign attaches the identity and removes it
      from the queue; mark-bot creates an `is_bot` person; exclude sets `exclude_from_metrics`; a GET on a
      mutation URL is 405; the empty state renders when nothing is unmapped.
- [x] **T15: People UI — merge.** `person_merge` view + form + confirmation. Tests in `test_views_people.py` —
      merging through the UI re-points identities and deletes the source; merging a person into itself is
      refused with a visible error; a lead is refused.
- [x] **T16: Ukrainian parity + CSS.** `make messages`, translate every new msgid, `make css`. Tests: existing
      `tests/test_translations.py` and `tests/test_css_tokens.py`, plus
      `test_views_people.py::test_uk_render_has_no_canary_english` (mirrors the phase-3 canary test).
- [x] **T17: Docs.** `docs/user/map-people.md` (task-shaped: what the queue is, how to assign, when to merge,
      what a bot flag does to the numbers), a `CHANGELOG.md` entry, and the derived-field definitions appended
      to `docs/METRICS.md`'s source-of-truth note or `docs/DECISIONS.md` as the phase requires. Test:
      `tests/test_docs.py` extended so every derived field name in `derive.py` is mentioned in the docs.

---

## Verification

```bash
# project gate (CLAUDE.md "lint" + "test")
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py check
uv run pytest -q

# migrations from zero, including 0002_commit_author_email_identity
rm -f /tmp/pr-radar-zero.sqlite3
DATABASE_URL=sqlite:////tmp/pr-radar-zero.sqlite3 uv run python manage.py migrate --noinput
rm -f /tmp/pr-radar-zero.sqlite3

# generated artefacts (both diffs must be empty after committing)
make css      && git diff --stat static/css/app.css static/css/.build-manifest.sha256
make messages && git diff --stat locale/

# e2e
make e2e-up && uv run pytest e2e -q; make e2e-down
```

| # | Acceptance criterion | Test that proves it |
|---|---|---|
| 1 | Each derived field has a test over hand-built fixtures | `apps/activity/tests/test_derive.py` — one test per field (T6–T9), all over factory-built rows, no HTTP |
| 1a | …a never-drafted PR where `ready_for_review_at == created_at` | `test_derive.py::test_never_drafted_pr_is_ready_at_creation` (+ `::test_drafted_pr_keeps_the_timeline_value`, `::test_still_draft_pr_has_no_ready_time`) |
| 1b | …a revert chain | `test_derive.py::test_revert_chain_links_reverts_pr` (+ `::test_reverts_by_number`, `::test_unresolvable_revert_has_no_target`) |
| 1c | …an excluded-path-only PR with `effective_lines == 0` | `test_derive.py::test_excluded_only_pr_has_zero_effective_lines` |
| 1d | …a rubber stamp at exactly the 10-minute boundary | `test_derive.py::test_rubber_stamp_boundary_is_exclusive` (10m00s → `False`, 9m59s → `True`) |
| 2 | Merging two `Person` records re-points every `Identity` and leaves no orphan identity or PR | `apps/catalog/tests/test_person_merge.py::test_merge_repoints_every_identity`, `::test_merge_leaves_no_orphan_identity_or_pr` (counts before/after + `Identity.objects.filter(person=None)` unchanged + every PR's `author.person` resolves) |
| 3 | A bot-authored PR is flagged `is_bot` and appears in the separate bot counter rather than in metrics | `apps/catalog/tests/test_identity.py::test_bot_login_auto_creates_a_bot_person`; `apps/activity/tests/test_selectors.py::test_bot_pr_is_out_of_the_metric_population_and_in_the_bot_counter` |
| 4 | The unmapped-identity queue lists a seeded unmatched email and its page count matches the model count | `apps/catalog/tests/test_views_people.py::test_queue_lists_the_unmatched_email`, `::test_queue_count_matches_the_model_count` |
| 5 | Re-running derive over a PR produces identical values — no drift on a second pass | `apps/activity/tests/test_derive.py::test_second_derive_pass_changes_nothing`; `apps/github_sync/tests/test_pipeline.py::test_second_sync_leaves_derived_fields_identical` |
| — | Deliverable: auto-mapping from noreply and from GitHub-linked commit emails | `test_identity.py::test_noreply_email_adopts_the_login_person`, `::test_github_linked_commit_email_adopts_the_login_person`; `test_upserts.py::test_commit_email_identity_is_written` |
| — | Deliverable: bot detection from the configurable login list | `test_identity.py::test_is_bot_login_*` (suffix, exact, case, negative), driven through `AppSetting` overrides |
| — | Deliverable: queue actions (assign, create person, merge, mark bot, exclude) | `test_views_people.py` (T13–T15) |
| — | Deliverable: `derive` wired into `process_pull_request` | `apps/github_sync/tests/test_pipeline.py::test_hook_resolves_identities_then_derives` (+ the existing containment test) |
| — | Deliverable: Settings → People UI | `test_views_people.py` admin/lead pair on every URL; `tests/test_urls_login.py` anonymous walk |
| — | Convention: every selector starts from `scope_for_user()` | `apps/activity/tests/test_selectors.py::test_selectors_take_a_scope_filter`; `apps/catalog/tests/test_selectors.py` |
| — | Convention: Ukrainian parity in the same phase | `tests/test_translations.py` + `test_views_people.py::test_uk_render_has_no_canary_english` |
| — | Convention: no N+1 on a list view | `test_views_people.py::test_people_list_query_count`, `::test_queue_query_count` |
| — | Convention: no live GitHub call | root `conftest.py` respx guard (unchanged, applies to every new test) |

### Case selection (per `.autodev/guides/case-taxonomy.md`)

**Happy path:** sync fixtures → author mapped to a person → every derived field populated. **Input and
boundaries:** size buckets at exactly 10/100/400/1000, the 10-minute rubber-stamp boundary, `effective_lines
== 0`, no file rows at all (`None`, never `0`), an email with no `+`, an uppercase login, a glob without a
separator. **State:** empty queue (empty state), one unmapped email, many (pagination), a person with no
identities after a merge. **Errors:** merging a person into itself, assigning to a deleted person, an invalid
glob pattern in a setting — each asserted by the visible message. **Permissions and identity:** every new URL
gets an admin success *and* a lead refusal, plus the global anonymous walk. **Idempotency and repetition:**
the second derive pass, the second resolution pass, a second sync, a double-submitted assign.
**Persistence:** a lead's assignment survives a re-sync (never re-pointed). *Deferred, not authored:*
concurrency (single-writer SQLite, one sync at a time — phase 3's lock), accessibility beyond the existing
page smoke tests (phase 11), async chains (nothing in this phase is queued).

---

## Risks

| Row | How this phase touches it | What the plan does |
|---|---|---|
| **1 — the tool measures people** | This is *the* phase that decides who a PR belongs to. A wrong mapping or a missed bot puts a false number in a 1:1. | A GitHub login is auto-mapped because it is a fact; a bare email is never guessed — it goes to a visible queue with a counter. A lead's assignment is never overwritten by a sync (`person is None`-guarded). Bots and `exclude_from_metrics` people leave the metric population through one selector, and are counted separately, with tests for both directions. Every derived-field rule has a hand-built fixture test, including its boundary. |
| **7 — metric correctness silently breaks** | Every phase-7 metric reads these cached fields. | Absent input yields `None`, never `0` (spec §15), asserted per field; derive is a total function of stored rows, re-run-identical by test; `derive_pull_requests(queryset)` exists so phase 7's `recompute` rebuilds instead of drifting. |
| **8 — Ukrainian lags** | Two new pages and ~40 new strings. | T16 is a task, not a cleanup: `make messages`, full translation, plus the canary test on the `uk` render. |
| **15 — generated artefacts go stale** | New templates ⇒ `app.css`; new strings ⇒ `.po`/`.mo`. | `make css` and `make messages` are in T16 and in Verification, and the existing freshness tests fail the build otherwise. |
| **10 — dashboards miss the budget** (early touch) | The People list and the queue are the first real list views. | `assertNumQueries` on both, `select_related`/`prefetch_related` on the identity→person walk, pagination on the queue. |
| **3 — a restricted lead sees another project's data** (early touch) | New selectors are the first ones after `accounts`. | Both new `selectors.py` modules take a `ScopeFilter` and never call `objects.all()`, so phase 8 narrows them in one place. |

---

## Out of scope

- **`merge_method`** stays `unknown`. No metric in spec §8.2 reads it; only churn does, and the parent-count
  evidence that determines it lives in the git clone phase 10 owns. Deriving it from GraphQL alone would be a
  guess written into a cached field.
- **`manage.py recompute`** and dirty-day marking (phase 7). This phase ships `derive_pull_requests(queryset)`
  as the callable that command will wrap.
- **AI fields** (`ai_status`, `ai_tools`, `ai_disclosure`) — phase 5; **`PRFile.matched_sensitive_rule`** —
  phase 6; **`CheckStatus` first-pass reading and `followup_fix_rate`** — phase 10.
- **Metric aggregation.** `pull_requests_for_metrics()` defines the population; `metrics.compute()` (phase 7)
  is the only reader of it, and the unmapped/bot counters reach the dashboard in phase 8.
- **`reviewer_response_p50`** needs `REVIEW_REQUESTED_EVENT` timeline items, which `PR_TIMELINE_QUERY` does not
  request and no model stores. Phase 7 must extend the query, the fixtures and the schema — flagged here so it
  is not discovered late. `is_rubber_stamp` deliberately measures from `ready_for_review_at`, per spec §8.2, so
  it does not depend on that work.
- **People pages for leads** (`/people/<id>` profile, review load) — phase 9.
