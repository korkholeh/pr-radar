# Changelog

## Unreleased

### Added

- **Sync runs by itself every hour.** The background worker (`run_huey`) now starts a sync at the top of every
  hour, recorded on the Sync page with the "Schedule" trigger. Before, the worker only ran a sync when someone
  pressed **Sync now**, even though the setup guide said otherwise. An hour whose run finds another sync still in
  progress is skipped. A cron or launchd `manage.py sync` entry is no longer needed while the worker is running.

- **The pull request list flags violations at a glance.** The Violations cell carries a traffic-light icon: a
  green check when the pull request has no open policy violations, an amber alert for exactly one and a red alert
  for two or more. The same icon shows in the pull request table on the dashboards. Exports are unchanged.

- **Dashboards explain what makes a pull request "AI".** A collapsed block under the filters on every dashboard page
  walks through the five AI statuses in the order they are decided — explicit, disclosed, suspected, no AI,
  unknown — says which of them are in the AI cohort, and lists the tools the active high-confidence rules
  recognise. It reads the live settings, so turning on `AI_COHORT_INCLUDE_SUSPECTED` or changing
  `AI_SUSPECTED_MIN_STRUCTURAL_KINDS` changes what it says. Admins get links to the rule pages from it.

- **The person page recommends what a lead should do.** A "Recommendations for the lead" block under the charts
  lists numbered action items, most important first: open high-severity policy violations to go through this
  week, rules broken three or more times in the period, other open violations to triage, then quality and flow
  gaps — follow-up fixes, 21-day churn, rework, lead time, time to first review, PR size — each with the person's
  value, the baseline and a concrete next step, and a nudge into the review rotation for someone who merges but
  never reviews. Metrics at least 20% better than the baseline are listed under "Worth acknowledging". The
  baseline is the person's primary project, or the organization when they have none. Metric items need
  `MIN_SAMPLE` merged pull requests; below that only policy items are listed and the block says why. The items
  come from fixed rules over the comparison and violation tables on the same page, so they cannot disagree with
  them.

  An **AI adoption** section of the same block covers AI separately. It says whether the person uses AI more
  than, about as much as or less than the team (their AI share of merged pull requests against the baseline),
  how their pull requests opened in the period split into explicit, disclosed, suspected and unmarked, and which
  tools they named. Its action items: violations of the AI rules (disclosure, allowed tools, AI review, human
  approval, AI PR size, sensitive paths and the rest — listed here instead of among the general items),
  pull requests that look AI-assisted but are not disclosed, AI-assisted pull requests that hold up worse than the
  person's own other pull requests (follow-up fixes, reverts, churn, rework, CI first pass, test changes, size —
  only where both sides have `MIN_SAMPLE` pull requests), AI work on high-risk paths, and — only when the team
  itself uses AI for at least 10% of its pull requests — a question about what keeps the person from using it,
  framed as removing blockers rather than raising a number. Someone whose AI-assisted work holds up and has no
  open AI violations is suggested as a person to show the team how they work.

- **The person page shows how far a person is from their team.** In the comparison table, under each value in
  the Person column, two lines give the gap to the project and to the organisation — percentage points for a
  share, a relative change for a duration or a size — coloured by the metric's direction and grey on a small
  sample. Pull requests merged and reviews given get none: their baselines are totals, not a typical person. A
  legend under the table says what the Project and Organization columns are — medians and rates over every pull
  request at that level, never an average of people — and what the colours and "Small sample" mean.

- **The person page counts policy violations by rule.** A new table lists every rule this person's pull requests
  broke in the period, highest severity first, with the total and how many of those violations are now open,
  acknowledged, waived or resolved, and an "All rules" row underneath. What to raise in a 1:1 — and what has
  already been dealt with — is readable without filtering the Policy console.

- **The author × reviewer heat map points at the gaps.** It scrolls sideways on its own, with the author column
  pinned, so a large team no longer widens the page. Each author carries their pull requests merged in the
  period, each reviewer the pull requests by others they reviewed, and cells now count distinct pull requests
  rather than review rounds. A reviewer far below the team's median (`REVIEW_LOW_ACTIVITY_PCT`) and an author
  whose merged pull requests others rarely review (`REVIEW_LOW_COVERAGE_PCT`, from `MIN_SAMPLE` merged) are
  flagged. Authors nobody reviews now get a row, and authors who never review a zero column. Where author and
  reviewer are one person the cell is muted, unless they reviewed their own pull request: that self-review is
  highlighted.

- **The pull-request timeline shows how long each step took.** Every event after the first carries the time since
  the one before it — "+3 minutes", "+1d 4h" — next to its date, so where a pull request sat waiting (for a
  first review, for the merge after approval) is visible at a glance.

- **The size bucket says what it means.** On the pull-request page the size bucket now carries its line range —
  "S (10–99 lines)", "XL (1,000 lines or more)" — read from the `PR_SIZE_BUCKETS` setting, so a custom boundary
  shows as configured.

- **The pull-request page shows the description.** A collapsible block under the header renders the pull
  request's Markdown body — headings, lists, task lists, code blocks, tables, links — so checking why a policy
  rule fired no longer needs a trip to GitHub. HTML comments left over from a PR template are hidden, as GitHub
  hides them. Raw HTML in a body shows as text, and images show as links rather than being loaded.

- **Reviewer load separates reviews from the pull requests behind them.** The Reviews page's chart now draws two
  bars per reviewer — reviews given, and the distinct pull requests those reviews fall on — and the table and its
  exports carry both numbers as sortable columns. 43 reviews across 17 pull requests and 43 across 43 are very
  different loads, and until now the page showed them as the same number.

- **Every chart and KPI card says what it measures.** Their titles now carry an info icon that opens the
  explanation: for a chart, which pull requests it is drawn from, how the value is computed, what the buckets on
  the x axis are and the caveat worth knowing — that a percentile describes its own bucket only, that 21-day
  churn stays empty until `manage.py compute_churn` has run, that violations are counted in the bucket they were
  opened in. A KPI card shows its metric's own definition, the same sentence `docs/METRICS.md` documents, plus
  which direction is the good one. It opens on hover and on keyboard focus, in both interface languages.

- **The pull-request page names its author.** The header now carries the author under the title, linking to their
  person dashboard when that person is one you have access to. A GitHub login with no person mapped to it yet is
  shown as the login, with a note saying so.

- **Projects can be created and edited in the app.** The **Projects** page has a **New project** button and an
  **Edit** link on every row, for anyone who may manage settings — a project's name, slug, description,
  repositories and active flag, with the slug derived from the name when you leave it empty. Until
  now a project existed only if someone made it in the Django admin or a seeding command. Both actions are
  recorded in the audit log.

### Changed

- **Suspected AI pull requests are in the AI cohort.** `AI_COHORT_INCLUDE_SUSPECTED` now defaults to on: a pull
  request only medium- or low-confidence rules matched, or that tripped two kinds of structural signal, counts as
  AI in AI PR share, the AI/non-AI comparisons and the AI-specific policy rules. An installation that saved the
  old value keeps it until it is changed on the settings page. After changing it, run `manage.py recompute`: the
  cohort rollups on disk and the violations the policy engine already raised were computed under the old value.
  Changing the setting now also refreshes the dashboards straight away instead of after the metrics cache expires.

- **The disclosure rate is off the dashboards.** It counts AI pull requests whose author ticked a level in the "AI
  assistance" section of the recommended PR template (`docs/pull_request_template.md`), so until a team adopts that
  template it reads 0% for everyone — which looked like a finding and was not one. It is gone from the KPI row, the
  AI adoption chart, the project, repository and people tables, the person comparison and the report's Trends
  sheet. The metric itself stays in the registry and in `docs/METRICS.md`, and the disclosure policy rules are
  unchanged.

- **The Latency chart draws the p90 lines only.** Lead time and time to first review each keep their 90th
  percentile — how long the slow pull requests took; the medians are on the KPI cards above, and four lines
  made the chart hard to read. The report's Trends sheet, built from the same chart, follows: its latency columns
  and chart are the two p90s.

### Fixed

- **Violations are counted by the day their pull request was opened, not the day PR Radar recorded them.**
  A sync or recompute writes hundreds of violations in one run, so "new violations in the last 30 days" counted
  almost every violation right after a first sync or recompute, whatever the age of the pull request. The
  "New violations" and "Open violations" metrics, the "Violations by rule" metric, the Policy page's KPI card
  (now "Violations on PRs opened in the last 30 days"), by-rule chart and date filter ("PR opened from" / "PR
  opened to"), the person page's violation table and the report's Violations sheet now all use the pull
  request's opening date. The Violations sheet gains a "PR opened" column; its "Created" column is renamed
  "Recorded". Run `manage.py recompute` to rebuild the daily rollups.

- **A merged pull request no longer looks "open" in the Policy table.** The Status column is the violation's
  triage state — "open" means no lead has judged it yet — but in Ukrainian it shared its translation with an open
  pull request, so violations on merged pull requests read "Відкрито". The column (and its filter) is now
  "Violation status", an unjudged violation reads "Не розглянуто" in Ukrainian, and the pull request's own state —
  open, merged or closed — shows under its link.

- **The Policy table's "Age" column is replaced by the pull request's date.** It showed the time since the
  violation row was written, and a sync or recompute writes hundreds of rows in one run, so most rows read the
  same "2 days, 3 hours". The column is now "PR opened" — the pull request's creation date, with the date the
  violation was recorded in its tooltip — and the table lists the newest pull requests first. On the pull request
  page the same column is now "Recorded", with the date and time instead of an elapsed duration.

- **The Policy page's by-rule chart no longer disagrees with the table without saying why.** The chart counts
  every violation raised in the period, in any status; the table below lists open violations only by default. A
  rule whose violations had all been resolved showed a bar and an empty table. The chart is now titled "New
  violations by rule", states its dates, splits each bar into open and acknowledged/waived/resolved with an
  "open / total" count, and each rule's name opens the table on exactly the violations its bar counts.

- **"Tool not allowed" no longer fires for signals that name no tool.** Behavioural and stylometric signals — a
  commit burst, mass file creation, one large commit, an agent-configuration file — say a pull request looks
  AI-made without naming a tool, and were filed under "Other". With "Other" left out of the allowed tools, every
  such pull request got a "Tool Other is not allowed" violation. Now only a tool the author declares, or one that
  detection actually names, is checked. The violation text also says where the tool came from — declared by the
  author, identified by detection, or both — and shows the tool's name instead of its code. Run
  `manage.py recompute` to clear existing false violations.

- **Lead time, time to first review, cycle time and reviewer response time show real durations.** The calculators
  returned hours while every screen, chart and export read the value as seconds, so a 2h 43m median lead time
  showed as "3 seconds" — and a CSV or XLSX export showed it as 0.0 hours. They now report seconds, the unit
  everything downstream already expected. Cached results from before the fix are not reused.

- **A larger PR size now reads as worse, not neutral.** `PR size (median)` is marked lower-is-better: its KPI card
  delta, its export delta column and the person page's gap to the project and organisation are now green when the
  pull requests got smaller and red when they grew, instead of always grey.

- **Review waiting times read as days and hours.** The Reviews page's "Waiting" column showed a bare hour count —
  "173h" — with an untranslated "h". It now reads "7d 5h" past a day, "5h 12m" under one and minutes under an
  hour, in both interface languages, the same format the pull-request page uses for its durations.

- **A task key in the title now counts as a linked task.** `TASK_LINK_MISSING` looked for a task reference in the
  pull-request description only, so a title such as `[ENG-175] treat AptPay 200 as a successful bank/transit
  verification` with no key repeated in the body was flagged. The rule now accepts a reference in the title or
  anywhere in the description. Run `manage.py recompute --skip-rollups` to clear the violations already raised.

- **`recompute --skip-rollups` left dashboards showing the old numbers.** It re-evaluated pull requests but only
  a rollup rebuild invalidated the metric cache, so violations it resolved still counted on every compliance
  chart and KPI until the cache expired. It now invalidates the cache after evaluation as well.

- **A resolved violation looked current on the pull-request page.** The page listed resolved violations in the
  same table as open ones, with a checkbox to acknowledge them, while the pull-request list counted only open
  ones. Resolved violations now sit in a collapsed "resolved" block below, as history with no actions. The
  dashboard's recent pull requests table also counted resolved violations; it now counts open ones, like the
  pull-request list.

- **Chart tooltips were readable in one theme only.** The tooltip that follows the cursor over a chart was
  painted from an inverted surface — dark in the light theme, light in the dark theme — while Chart.js drew its
  text in white, so in the light theme the numbers were white on white. Tooltips now use a popover surface that
  follows the theme, with ordinary body text, and so does the new info bubble.

- **Links to GitHub were broken for any pull request numbered over a thousand.** A pull request
  number was rendered as an ordinary number, so PR 1208 read `#1,208` — and the link beside it on
  the pull-request page pointed at `https://github.com/owner/repo/pull/1,208`, which GitHub does
  not resolve. Day lists, the reviews table, the pull-request page and both policy lists show the
  number as the identifier it is now.

- **Vendored and generated files no longer inflate the structural AI signals.** The structural
  detectors read every file a pull request touched, including the ones excluded from every size
  metric — a checked-in `vendor/` bundle or a regenerated lockfile counted towards mass file
  creation and the duplicated-block search like hand-written code. They are left out now, the same
  way they already were everywhere else. Signals recorded before this are unchanged; run
  `manage.py recompute` if you want the affected pull requests re-read.

- **Three evidence sentences were missing their Ukrainian translation.** The commit-burst,
  duplicated-block and instant-review-response sentences were never extracted into the catalogue,
  so a lead reading the interface in Ukrainian saw those three in English while every other
  sentence on the page was translated. They are translated now.

- **`config.settings.prod` refuses the published development `SECRET_KEY`.** It only ever refused a
  *missing* key, so a deployment that copied a developer's `.env` — or set `SECRET_KEY` to the
  documented default by hand — started normally and signed its sessions and CSRF tokens with a key
  that is in this repository. An empty key is refused too. Both fail at startup with a message
  saying what to do.

- **A read-only fine-grained token no longer fails the whole sync over one permission it lacks.** GitHub
  reports a field the token may not read as an error alongside the data it did resolve, and PR Radar treated
  every such report as a dead credential: the run stopped with "authentication failed", the connection was
  marked invalid, and its remaining repositories were skipped. The commonest case is `statusCheckRollup`,
  refused to any token without **Checks: read** — the pull requests, reviews and commits it *can* read now
  sync as normal and the check state is simply absent. A refusal on the repository itself still stops that
  connection, as it should. Sync errors also name what was refused now, instead of only saying that something
  was: **Settings → Sync** shows GitHub's own message and the field it applies to.

- **Private organization repositories now show up in discovery for a fine-grained token.** A fine-grained token
  whose resource owner is the organization reads its repositories fine, but GitHub's GraphQL API never listed
  them — it answers that list through the authenticated user's affiliations, and the user has none with
  repositories granted to the token. **Settings → Discover repositories** came up empty (or short) and the
  connection check reported "cannot see any repository" while the same token worked in every other respect.
  Listing now goes through the REST API instead: the account's own repositories plus the repositories of every
  organization the token can see. The connection check counts that same list, so the two can no longer
  disagree. Nothing to re-do — reopen the discovery page and the missing repositories are there.

- **AI reviewers and agents whose login has no `[bot]` suffix are now recognised as bots.** GitHub's
  `copilot-pull-request-reviewer` and the Charlie agent accounts (`charliecreates`, `charliehelps`) were
  being counted as people, so their reviews inflated the review metrics of the teams they work on. They are
  now in the `BOT_LOGINS` default. The flag is only decided when a person is first created, so existing
  databases need one run of the new `manage.py reclassify_bots` command — it lists the people it would
  change, writes nothing without `--apply`, and never turns a person a lead marked as a bot back into a
  person. Run `manage.py recompute` afterwards to refresh the metrics.

### Added

- **Settings → AI policy explains itself.** Every switch on the page now carries the two or three lines a
  lead needs to decide about it: which violation code it raises, at what severity, on which pull requests,
  and what else has to be true before it can fire at all — that AI review needs a reviewer login named, that
  the high-risk plan check needs a sensitive-path rule at high risk, that a merge-dependent check says
  nothing about an open pull request. Each group of switches gained a sentence of its own, and the page now
  opens by saying that saving publishes a new version which governs what comes next rather than rewriting
  the verdict on work already done, and that violations are recomputed on the next sync. In English and
  Ukrainian alike. The long form is still [`docs/POLICY.md`](docs/POLICY.md) and
  [`docs/user/ai-policy.md`](docs/user/ai-policy.md).

- **Five compliance numbers on every dashboard.** A fourth KPI row: **bot-only approval rate** (of the pull
  requests merged with any approval, the share whose approvals came only from bots), **quality-gate bypass
  rate** (merged with the last check rollup red, counted only where a repository runs checks at all), **AI
  review coverage** (the share an AI reviewer you named actually reviewed) and **high-risk AI PR rate**, with
  the **structural signal rate** underneath the last one. They are on Overview, a project, a repository and a
  person alike, and they export with everything else.

  **They do not wait for you to switch a check on.** Each one reads the same facts the matching PLANEKS check
  reads, so you can watch a number for a fortnight and then decide what to enforce — a rate that only moved
  when somebody ticked a checkbox would measure the configuration rather than the engineering. Two depend on
  your settings for a different reason: AI review coverage is blank until you name an AI reviewer login (with
  nobody named, "no AI reviewer looked" is a missing setting, not a fact), and the high-risk rate needs a
  sensitive-path rule carrying `risk_level=high`.

  **The person page now leads with compliance and quality, and keeps volume last** — the standards' own
  constraint that generated lines and PR counts are not a measure of a person, applied to the page a lead is
  most likely to open before a one-to-one.

  New reading: [`docs/user/ai-policy.md`](docs/user/ai-policy.md) walks the policy form group by group and says
  what to switch on first; `docs/METRICS.md` has the five formulas.

- **Author baselines: is this person's recent work unusual *for them*?** Four more structural kinds, computed
  nightly (`compute_baselines`, 01:00) rather than during a sync, because the answer changes when other pull
  requests arrive: **throughput jumped** against the author's own trailing rate, **volume moved outside their
  usual hours**, the **test-to-code ratio stopped varying**, and **descriptions broke from their own style**.

  These are the most consequential heuristics in the product and they are built to be hard to misuse:

  - **A team-wide change fires nothing.** `throughput_shift` divides the author's change by the team's over the
    same two windows, so a sprint, a release crunch or a return from holidays moves everybody and flags nobody.
  - **"Usual hours" are the author's own**, taken from their earlier commits and read in your reporting
    timezone. Nothing assumes a nine-to-five; a night owl's baseline is their own nights, and only a change
    away from it counts.
  - **No baseline below `MIN_SAMPLE` pull requests of history.** A person with four pull requests has no
    baseline, and the tool does not invent one for them.
  - **An author who never writes tests does not trip the ratio rule** — that is a policy question, not an
    authorship signal — and a bot never gets a baseline at all.

  They are `low` confidence, they ship deactivated like every structural rule, and each one's notes say what
  will trip it innocently: a new project, a new timezone, a newborn, a changed PR template. Read every match
  before you act on one; these describe a change in how somebody works, and most reasons for that are ordinary.

  **This can change `ai_status` on pull requests you already have.** A structural signal alone still changes
  nothing, but a baseline signal plus a per-PR one is two distinct kinds, which is the default threshold for
  `AI suspected`. Nothing moves until you activate a rule; after you do, run `manage.py recompute --baselines`
  and expect some historical pull requests — and therefore some AI metrics — to shift.

- **The PLANEKS AI Engineering Standards as automatic checks.** Fifteen new policy rules: a bypassed quality
  gate, weakened tests, an AI-only approval, a missing or unanswered AI review, a high-risk change with no plan, a
  description with no risk level, no verification note or no task link, a new dependency in an AI PR, a migration
  no designated reviewer approved, a committed credential file, a changed agent configuration, scope creep, and a
  rubber-stamped AI PR. `docs/POLICY.md` maps each one to the standard it comes from.

  **Every one of them is off until you turn it on.** Upgrading raises nothing — there is a test that builds the
  worst pull request this codebase can describe and asserts that a default policy produces zero violations for
  it. Settings → AI policy now groups its switches into sections (disclosure, human review, AI review, what a
  description must state, quality gates, size and scope) and starts prefilled from the version in effect, so
  publishing a new version means changing what you meant to change.

  Not every check is about AI. A bypassed quality gate, a weakened test, a committed credential and a missing task
  link are no better for having been written by hand, so those apply to every pull request — restricting them to
  the AI cohort would measure the tool rather than the engineering.

  **Risk levels.** A sensitive-path rule can now carry a risk level, and a pull request's risk is the highest of
  the paths it touches. Three things read it: the high-risk plan requirement, `high_risk_min_approvals` (which
  only ever raises the ordinary minimum, never lowers it), and an optional size limit per risk level.
  `manage.py seed_sensitive_paths` seeds the standards' own risk table — migrations, auth, payments, billing, CI
  configuration, settings, infrastructure — as **advisory** rules that classify risk and raise no violation of
  their own, so seeding changes nobody's compliance until you switch on a check that reads risk.

  **What PR Radar still will not tell you.** AI cost per task and your team's own rating of the tool are not
  derivable from GitHub data, and nothing here invents them. The standards' own rule that generated lines, prompt
  counts and AI PR counts are not a measure of an employee's performance is recorded in `docs/POLICY.md` as a
  product constraint: the person page leads with compliance and quality, and no metric ranks people by volume.

  The sync now also records whether each review thread was resolved and when a review was first requested, both
  from GraphQL fields it already had access to. A thread whose resolution an older GitHub Enterprise Server does
  not report stays *unknown* rather than becoming "unresolved" — nobody is accused of ignoring a reviewer on the
  strength of a missing field.

- **Diff-level signals: four kinds that read the change itself.** `wholesale_reformat` (a large diff that
  changes almost nothing once whitespace is ignored), `comment_density_outlier` (added code carrying far more
  comments and docstrings than the same files did before), `duplicated_blocks` (one block pasted across
  several files), and `unused_new_dependency`, which was registered but silent until now because deciding
  whether anything imports a package needs the lines of the change.

  **They cost no GitHub API call.** They read the bare clone the churn job already makes, so they run inside
  the nightly churn run (`manage.py compute_churn`, 02:00; `--no-diffs` skips this half) and take what is left
  of a repository's time budget after churn itself.

  **They are opt-in per repository.** Add repositories to the new `DIFF_ANALYSIS_REPOSITORIES` setting as
  `owner/name`, or a single `*` for all of them; the default is empty, so an upgrade analyses nothing until
  you ask it to. Reading the contents of every change is a choice an operator makes, not a default.

  The usual guarantees hold: these are structural rules, so they can never be `high` confidence, one signal
  alone never moves a pull request's AI status, and every one of them ships deactivated. A repository with no
  usable clone that night produces no signals **and deletes none** — a repository PR Radar could not reach is
  not evidence that a signal has gone away. Rebase merges are skipped, as they are for churn.

  Each analysis also records what the diff contains — weakened tests, relaxed CI configuration, files that
  look like committed credentials — as data for the policy checks arriving next. Counts, paths and codes only:
  a credential that leaked into a pull request is never copied into PR Radar's own database.

- **A second kind of evidence: what a pull request's shape says.** Detection rules match text a tool wrote.
  The new **structural signals** read the pull request itself — a large change merged within two hours of its
  first commit, five substantial commits ninety seconds apart, a whole change in one commit, ten new files
  across three directories, a new dependency nothing imports, three commits landing minutes after review
  comments. Settings → **Structural signals** lists them, and each one is tuned by thresholds you edit, with a
  dry run over your recent pull requests before you save.

  Two guarantees, and neither is a setting you can turn off:

  - **A structural rule can never be high confidence.** The database refuses it, not just the form. Only an
    artefact the tool itself wrote proves AI authorship; a heuristic about a pull request's shape is evidence
    for a human to read, so no combination of these signals ever makes a pull request `AI explicit`.
  - **One structural signal on its own changes nothing.** It takes `AI_SUSPECTED_MIN_STRUCTURAL_KINDS`
    (default 2) *distinct kinds* before a pull request becomes `AI suspected` — five commit bursts are one
    kind of evidence, not five. A lone signal is still written, still shown on the pull request page and still
    exported. It is evidence, not a verdict.

  Every shipped rule arrives **deactivated**, because a threshold that is right for one team is noise in
  another: a repository of generated clients bursts commits all day, and a team that squashes before pushing
  trips "whole change in one commit" on every pull request. Run `manage.py seed_signal_rules`, dry-run each
  rule against your own pull requests, adjust the numbers, then switch it on.

  Each signal explains itself in a full sentence that quotes both the measurement and the threshold that
  produced it — "600 lines across 12 files arrived 0.7 hours after the first commit (the rule flags 400 lines
  and 8 files within 2 hours)" — in English or Ukrainian, because the sentence is generated at read time from
  a stored code and its parameters rather than saved as English. Re-tune a threshold and the old signal is
  retired and replaced rather than left quoting numbers nobody uses any more.

  Outcome data is deliberately *not* an input: churn, reverts and follow-up fixes are what PR Radar measures
  **for** the AI cohort, so using them to decide who is in that cohort would make the AI-quality dashboards
  prove themselves. A test enforces it.

- **Every repository now shows whether it is set up for an AI agent.** Each sync reads the tip of the
  repository's default branch once and records which agent-configuration paths it carries — `.agents/`,
  `.claude/`, `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`, `.cursorrules` and the rest of
  `AI_TOOLING_PATH_GLOBS`. The repository page lists what it found, and **Repositories** gains an
  **AI tooling** filter so you can ask which of your repositories are configured for an agent at all.

  This is deliberately **not** an AI signal and never moves a pull request's AI status: "this repository has a
  `CLAUDE.md`" is equally true of every PR in it, so as a per-PR signal it would drown the ones that actually
  tell PRs apart. A pull request that *changes* one of those paths is a different fact, and that stays a
  detection rule.

  The probe costs one request for a repository with no agent tooling, and one more for each configured
  directory it actually has. A repository whose default branch cannot be read keeps its previous answer rather
  than being recorded as having none, and "never checked" stays visibly different from "checked, found
  nothing".

- **Detection now reads the diff, the title, the reviewers and the merger, not just the prose.** Four new
  detectors join the eight that existed: **file path** (a path in the PR's own diff), **PR title**,
  **reviewer identity** and **merged-by identity**. They are ordinary detectors, so any rule — shipped or
  your own — can use them, and the dry run works for each.

  Sixteen rules come with them:

  - Four **file-path** rules. A tool's own artefact in the diff is the strongest evidence short of a commit
    trailer, because nobody types a chat transcript by hand: Aider's `.aider.chat.history.md`, a SpecStory
    transcript, and `.claude/settings.local.json` all resolve a PR to `AI explicit`. A fourth, disputed rule
    notes when a PR merely *touches* an agent's configuration (`.agents/`, `.claude/`, `AGENTS.md`,
    `.cursorrules`, `.github/copilot-instructions.md` and the rest) — editing an agent's instructions is not
    the same as using one.
  - Five **AI reviewer** rules (CodeRabbit, Copilot review, Gemini Code Assist, Qodo, Ellipsis). All five are
    disputed and ship deactivated: a bot review tells you who reviewed the change, never who wrote it.
  - Five **stylometric** rules over the PR body — an assistant sign-off, bolded bullet lead-ins, a "Key
    changes" heading, promotional adjectives, a first-person change report. All are `low` + disputed, so a
    match can never reach `AI explicit` on its own, and all ship deactivated so you can dry-run them against
    your own pull requests and see the false-positive rate for your team before switching any on.
  - An agent-prefixed **title** rule and a merged-by-an-agent rule, both disputed hints.

  A rule that reads the diff or the reviews reports **at most one signal per pull request**, so a 400-file PR
  produces one line of evidence rather than four hundred. Excluded files (lockfiles, vendored trees, generated
  output) never reach a rule. Run `manage.py seed_detection_rules` to pick the new rules up, then
  `manage.py recompute` to apply them to pull requests you already have.

- **Eleven more detection rules, and a reason to trust each one.** The shipped rule set now covers the
  session and task permalinks agents leave in a PR body or commit trailer (Claude Code, Codex, Devin, Jules),
  the co-author trailers Cursor, Copilot and the Gemini CLI write, Copilot's hidden body marker, CodeRabbit's
  review marker, and a robot-emoji hint. Every rule now records **where its pattern came from** — documented
  by the vendor, observed in your own synced data, or unverified. `manage.py seed_detection_rules` creates
  the unverified ones **deactivated** and tells you how many, so nothing starts marking pull requests as
  machine-generated on the strength of a guess. `docs/user/tune-ai-detection.md` explains how to confirm one
  with the dry run and switch it on; activating a rule survives every later re-seed.

- **Charlie is detected as an AI tool.** Pull requests and commits authored by the Charlie agent
  (charlielabs.ai) now resolve to `AI explicit` with Charlie named as the tool, instead of sitting at
  `Unknown`. Run `manage.py seed_detection_rules` to pick the two new rules up.

### Changed

- **Paging now shows the page numbers.** Every paginated list — the dashboard tables, the identity queue and
  the Policy console — carries the same pager: **Previous**, the first and last page, a window of pages around
  the one you are on with an ellipsis for each gap, then **Next**, so page 30 of a long table is one click away
  instead of twenty-nine. The current page is marked for screen readers, and the "Page N of M" readout stays.

- **The interface has been redesigned.** Every page now shares one visual system: a sticky application header
  with the primary destinations, a Settings menu holding the admin-only pages, a page container with consistent
  width and spacing, and cards for KPIs, charts, filters, forms and tables. Buttons, inputs, labels, badges,
  tables, links, empty states and alerts are shared component classes (`static/css/src/input.css`) rather than
  ad-hoc utility strings, so a change lands everywhere at once. On a narrow screen the header keeps every
  control — the navigation moves to its own scrollable row instead of a duplicated mobile menu. Colours still
  come only from `static/css/tokens.css`; the new surface, accent-tint and shadow tokens are defined there for
  both themes.

- **Filters moved into a side panel.** On every page that has them — the dashboards, Pull requests, Reviews,
  the Projects/Repositories/People lists and the Policy console — the filters are now behind a **Filters**
  button and open as a panel sliding in from the right, so the page itself is free for the data. A strip of
  badges under the title shows what is applied (the period, the cohort, how many projects or repositories are
  selected). Applying a filter closes the panel; **Reset** clears every filter. Links you have bookmarked keep
  working: filter state still lives entirely in the address bar.

- **Filters that take several values are now tag boxes.** Projects, repositories, authors, state, AI status, AI
  tool, size and violation status no longer need Ctrl-clicking a scrolling list: what you picked shows as
  removable tags, and the rest are in a searchable drop-down — useful once there are more repositories or people
  than fit on a screen. The keyboard works throughout (type to search, arrows to move, Enter to toggle,
  Backspace to drop the last tag, Escape to close).

- **Every date field has the same calendar.** Dates are picked from a calendar the app draws itself, in your
  theme and your language, instead of the one the browser supplies — which looked different in every browser
  and ignored both. It opens from the field or its calendar icon, moves by month with the arrows, has **Today**
  and **Clear**, and takes the arrow keys, Enter and Escape. A date can still simply be typed as
  `YYYY-MM-DD`. Filter controls are also sized alike now, so a select and a date field read as the same kind
  of control.

- **The header controls are smaller.** The theme control is now a single icon button that cycles system →
  light → dark, showing the preference in force; the language control is a compact `EN | UK` pill where both
  languages are visible at once. Both still work without JavaScript.

### Added

- **Repository settings** (admins, Settings menu): every repository PR Radar holds, the connection that syncs it
  and that connection's status, filterable by connection. **Change connection** moves one repository to any
  other active connection behind an explicit confirmation — useful when a replacement token is added as a new
  connection. Until now a repository could only be rebound from **Discover repositories**, which requires the
  new token to already see it. Rebinding changes only which credentials future syncs use; everything already
  synced is kept, and the move is recorded in the audit log.
- **Load historical data** on the Sync page (admins): re-fetch every pull request updated in the last 7, 14, 30
  or 90 days, or since a date you pick, for all active repositories or only the ones you select. It shares the
  lock, rate-limit budget and rollup rebuild with an ordinary sync, and its runs are listed as `Backfill` with
  the start date they used. The command-line equivalent, `manage.py sync --since`, is unchanged.

### Fixed

- **A person's name in the People table is now a link.** The name column now opens that person's dashboard,
  the way the Projects, Repositories and Reviews tables already did; the XLSX report's People sheet carries the
  same link.

- A connection whose token cannot read the repositories it tracks is no longer reported as healthy. The
  `PERM_PULL_REQUESTS` check now actually lists pull requests on one of the connection's repositories instead of
  being recorded as passing without a request behind it, and a 404 — GitHub's answer for a private repository a
  token may not see — is read as denied access (`PERM_PULL_REQUESTS_DENIED`, `PERM_CONTENTS_DENIED`) rather than
  an unexpected error. A classic token that lacks the `repo` scope while private repositories are tracked is
  flagged as `CLASSIC_PAT_NO_PRIVATE_SCOPE`. Previously such a connection verified as `ok` and every sync
  reported success having synced nothing.
- Repository discovery now lists every repository a connection's token can read, whoever owns it — a
  client-owned or other-organization repository you have access to is no longer hidden. A connection's owner
  login is a label only; it no longer filters the list. When the list spans several owners, an **Owner**
  dropdown narrows it, defaulting to all owners.
- A connection whose token can see no repository at all is now reported as `degraded` with the new
  `REPOS_VISIBLE_NONE` check and a hint, instead of passing verification with "0 repositories" and leaving an
  empty discovery page as the only symptom.

## 0.1.0 — 2026-09-19

The first release. Everything below is new.

### Breaking changes

None — there is no earlier version to break.

### What you must do to run it

- Set `SECRET_KEY` and `FIELD_ENCRYPTION_KEYS` in `.env` (copy `.env.example`) **before** creating a GitHub
  connection. Without `FIELD_ENCRYPTION_KEYS` a token cannot be stored; losing the key later means re-entering
  every token. See `docs/GITHUB_CONNECTIONS.md`.
- Run `uv run python manage.py migrate`, then `createsuperuser`. `migrate` also creates the `admin` and `lead`
  groups.
- Run the background worker (`uv run python manage.py run_huey`) alongside the web process. Without it, syncs,
  churn and large exports never run — though every one of them is also a management command you can run by hand.
- Back up by stopping both processes and copying `DATA_DIR`. Your projects, people, identity mapping, policy and
  violation decisions cannot be recovered by re-syncing GitHub. See `docs/SETUP.md`.

### Connect GitHub and get data in

- GitHub connections (Settings → Connections): add, edit, verify and deactivate a connection; tokens are
  encrypted at rest and shown only as their last four characters. An admin banner warns about invalid, expired
  or soon-to-expire connections.
- Repository discovery and rebinding (Settings → Repositories): browse the repositories a connection can see,
  add the ones you want synced, and move a repository to a different connection without losing its history.
- GitHub sync (Sync page, `manage.py sync`, and a background task): pulls pull requests, reviews, commits,
  checks and files incrementally, with per-connection rate limiting and retry handling. See
  `docs/GITHUB_CONNECTIONS.md` and `docs/user/connect-github.md`.
- Automatic identity resolution: a synced GitHub login is mapped to a person immediately, a git email only when
  GitHub itself links it to a login; bot accounts are detected from a configurable login list.
- People management (Settings → People): list every person with their identities, team, role and PR count; edit
  a person's details, bot flag and metrics exclusion; merge two people into one. See `docs/user/map-people.md`.
- The unmapped-identity queue (Settings → People → Unmapped identities): assign an email to an existing person,
  create a new person from it, mark it as a bot, or exclude it from metrics.
- Every pull request carries its derived fields (size bucket, effective lines, test-file flag, rubber-stamp,
  self-merge, hotfix, revert, review timing and counts), recomputed idempotently after each sync.

### AI detection and policy

- AI detection: eight regex-based signal detectors, a tolerant PR-template disclosure parser and a resolved
  `ai_status`/`ai_disclosure`/`ai_tools` per PR, recomputed idempotently after each sync. See
  `docs/user/tune-ai-detection.md`.
- Settings → Detection rules: list, create, edit and activate/deactivate a detection rule, with a dry run against
  recently stored PRs before saving.
- AI policy engine: nine configurable rules (missing/mismatched disclosure, a disallowed tool, a forbidden or
  extra-review sensitive path, missing human approval, self-merge, missing tests, an oversized AI PR) evaluated
  idempotently after every sync, with results that auto-resolve when the condition clears without ever
  overwriting a lead's own judgement. See `docs/POLICY.md`.
- Policy console (`/policy/`): compliance KPIs, a by-rule violation distribution, a disclosure-mismatch PR list,
  and a filterable, paginated violation table with bulk acknowledge/waive (a reason is required); every status
  change is recorded in the audit trail. See `docs/user/handle-policy-violations.md`.
- Settings → AI policy: a versioned, append-only AI policy (allowed tools, disclosure/approval/test requirements,
  size limit) — saving creates a new version rather than editing history.
- Settings → Sensitive paths: list, create, edit and activate/deactivate a path glob marked `forbidden` or
  `needs_extra_review`, globally or per project.
- `docs/pull_request_template.md`, a recommended PR template matching the disclosure parser's defaults.

### Dashboards and pages

- Dashboards (Overview, Projects, Repositories): KPI cards with delta arrows and sparklines, six charts
  (throughput, AI adoption, latency, PR size distribution, churn/rework, violations by rule), and sortable,
  searchable, paginated tables for projects, repositories, people and recent PRs. Every filter — period,
  granularity, cohort, and a Day mode showing one calendar day — is carried in the URL, so a filtered view can
  be shared as a link. See `docs/user/dashboards.md`.
- People page (`/people/`) and a Person page (`/people/<id>/`): the person's PRs, violations, review load, a
  comparison against their primary project and the whole organisation, and a private notes card visible only to
  leads. See `docs/user/people.md`.
- Pull requests page (`/prs/`): every PR in scope, filterable by author, state, AI status, tool, size and
  whether it has open violations, exportable to CSV/XLSX. See `docs/user/pull-requests.md`.
- PR detail page (`/prs/<id>/`): a chronological timeline, per-PR duration metrics, the changed-files list with
  test/excluded/sensitive-path badges, the resolved AI status with each matched signal's evidence, open
  violations with an inline acknowledge/waive action, and a status-aware churn section.
- Reviews page (`/reviews/`): reviewer workload, an author×reviewer heat map, and the list of PRs waiting for
  review. See `docs/user/reviews.md`.
- Sign in, sign out and reset a forgotten password. Theme switcher (System / Light / Dark), applied before the
  page paints so there is no flash of the wrong theme, and a language switcher (English / Українська) — both
  available on the login page, both saved to your account once you are logged in.

### Metrics and churn

- Metrics registry: 39 metrics across adoption, delivery flow and quality, each with a documented formula, unit
  and direction. See `docs/METRICS.md`.
- Churn analysis: a nightly job (`manage.py compute_churn`, 02:00) clones each repository and measures how much
  of a merged PR's own lines survive `CHURN_WINDOW_DAYS` after merge, feeding the `churn_21d` KPI, the
  churn/rework chart, and a status-aware Churn section on the PR detail page (a percentage, "not measured for
  rebase merges", "too large to measure", or a retried error — never a fake `0%`). See `docs/user/churn.md`.
- `followup_fix_rate` (heuristic): flags a merged PR as likely followed by a fix, by title/branch pattern and
  file overlap within a configurable window; shown labelled "(heuristic)" on the Person page's comparison table.

### Exports

- Table export to CSV and XLSX from any dashboard table, carrying the table's current filters, search and sort
  and covering every matching row, not just the current page. The XLSX file has a bold frozen header, an
  autofilter, real numeric cells, and clickable PR links.
- A seven-sheet dashboard report (Summary, Trends with native Excel charts, the page's own tables, PRs,
  Violations, Metrics reference, Parameters), downloadable as XLSX from Overview, Project, Repository and
  Person pages. See `docs/user/exports.md`.
- Background exports: a table or report export past `EXPORT_SYNC_MAX_ROWS` rows is queued as a job instead of
  blocking the request; "My exports" (`/exports/`) lists your own jobs with live status and a download link,
  `manage.py process_exports`/`cleanup_exports` are the worker-free fallbacks, and every export — synchronous or
  background — writes an audit entry.

### Access and honesty about numbers

- Per-project access restriction: a lead granted access to specific projects (Django admin → Accounts → User
  project access) sees only those projects' data everywhere — every page, chart, CSV/XLSX export and report;
  a lead with no grants continues to see everything.
- Page-level empty states: every dashboard, list and chart card explains *why* it's empty — nothing has ever
  been synced, or nothing matches the current period and filters — instead of showing a blank area, with an
  action link ("Go to Sync") where there's an obvious next step.
- Small-sample marking: any metric computed from fewer than five pull requests is greyed and labelled, on KPI
  cards and on metric table cells alike, with a title/aria-label carrying the full explanation — never colour
  alone. A metric with no data shows an em dash, never a zero.
- A colour-contrast retune for WCAG AA: two new design tokens (`--border-strong` for a control's own boundary,
  `--on-heat` for text on a heat-map cell) and a retuned light-theme palette, so every foreground/background
  pairing used in the app clears the 4.5:1 (text) / 3:1 (UI) contrast ratio in both themes.
- Long Ukrainian strings no longer clip or overflow on KPI cards or table headers.

### Performance

- The Overview page renders well under its 1.5-second budget at 50 repositories / 20,000 pull requests, down
  from roughly 29 seconds before this release's profiling and query-shape fixes.
- `manage.py seed_demo --scale large` seeds 50 repositories and 20,000 pull requests for trying the dashboards,
  and the app itself, at a realistic scale.
- `scripts/profile_dashboard.py` renders the Overview, Project, Repository, People and Reviews pages and reports
  wall time, query count and the slowest SQL statements per page.

### Commands and documentation

- Management commands: `sync`, `recompute` (`--rollups-only` / `--skip-rollups`), `compute_churn`, `seed_demo`,
  `seed_e2e`, `metrics_doc` (`--check`), `bootstrap_connection`, `rotate_encryption_key`,
  `seed_detection_rules`, `process_exports`, `cleanup_exports`.
- User documentation under `docs/user/` — a [guide index](docs/user/README.md), a task → page map, one page per
  feature, and troubleshooting for empty dashboards, small-sample greying, a stale "Data as of" banner, a failed
  sync and a missing churn number.
- Developer documentation: [`docs/dev/architecture.md`](docs/dev/architecture.md) and eight architecture
  decision records under `docs/dev/adr/`.
