# Tune AI detection

This page is for a lead or admin. PR Radar flags a PR's AI status (`ai_explicit`, `ai_disclosed`, `ai_suspected`,
`no_ai`, `unknown`) from two independent inputs: **signals** — regex rules matched against commits, the PR body
and metadata — and the PR's own **disclosure** (the "AI assistance" section of your PR template, if you use the
recommended one at `docs/pull_request_template.md`). If you don't see **Detection rules** in the top bar, ask an
admin for the `catalog.manage_settings` permission.

## Read a signal

Open a PR's page. The **AI signals** section lists every signal that matched: which detector found it, which
tool it points to, its confidence and an evidence fragment — the actual text that matched, up to 200 characters,
so you can judge a match without leaving the page. The resolved status is `ai_explicit` only when at least one
`high`-confidence signal fired; a `medium`/`low` signal alone yields `ai_suspected`.

Twelve detectors exist, each reading one part of the pull request:

| Detector | Reads |
|---|---|
| Commit trailer | the `Key: value` trailers of each commit |
| Commit author | each commit's author, committer and co-author names and e-mail addresses |
| PR author | the login that opened the PR |
| PR body footer | the PR description, with HTML comments stripped out |
| HTML comment | only what is *inside* the description's HTML comments — invisible to a reviewer |
| Label | each label on the PR |
| Branch pattern | the head branch name |
| Commit message | each commit message |
| File path | each non-excluded path in the PR's diff |
| PR title | the PR title |
| Reviewer identity | the login of each reviewer |
| Merged-by identity | the login that merged the PR |

The last four read collections rather than one string, so they are **bounded**: a rule that matches a file path
or a reviewer produces one signal per pull request — the first match — however many files or reviews there are.
A 400-file PR gives you one line of evidence, not four hundred. Files your settings exclude (lockfiles, vendored
trees, generated output) are never offered to a rule at all.

Two of those four deserve a warning. **Reviewer identity** tells you the change was reviewed by a machine, not
who wrote it, and **merged-by identity** tells you which account pressed merge — on many teams that is a bot on
every approved PR. Every shipped rule for both is marked disputed for that reason, which caps it at `low`
confidence so it can never resolve a PR to `ai_explicit` on its own.

## Why some shipped rules arrive switched off

The rule set PR Radar ships with is not one uniform list. Each rule records **where its pattern came from**:

- **documented** — the vendor's own documentation says the tool writes this string.
- **observed** — it has actually been seen in a pull request synced into this installation.
- **unverified** — it is a reasonable expectation that nobody has confirmed. Perhaps the vendor changed the
  wording, perhaps it was never written that way at all.

`manage.py seed_detection_rules` creates the documented and observed rules **active**, and the unverified ones
**deactivated**. The command tells you how many it switched off.

This is deliberate. An unverified rule is not a weaker signal — if a tool really does stamp a commit with its
own name, matching that stamp is proof. The uncertainty is about whether the string is ever written, not about
what it means when it is. So the rule keeps its confidence and simply waits for you to confirm it, rather than
being downgraded to `medium` and quietly turning every real match into a missed one.

## Confirm an unverified rule

You need one pull request that the rule should have caught.

1. Find a PR you already know was produced by that tool — ask the author, or pick one from a repository where
   the tool is in use.
2. Open Settings → **Detection rules**, find the rule, and run it through **Dry run**. It tests the pattern
   against the most recently stored pull requests without writing anything.
3. Read the result:
   - **It matched, and the evidence is the tool's own marker** → activate the rule. Note the PR (`repo#number`)
     somewhere; if you keep a fork of `fixtures/detection_rules.yaml`, move the rule's `provenance` to
     `observed` and put that reference in its `notes`.
   - **It matched something innocent** → edit the pattern to narrow it, or leave the rule off.
   - **It matched nothing** → that tells you nothing on its own, unless you are sure the PRs it scanned include
     one the tool wrote. Widen the dry run (`DETECTION_DRY_RUN_PR_COUNT`) or leave the rule off and revisit it
     when you have a known example.
4. After activating, run `manage.py recompute` so stored pull requests are re-evaluated. New signals appear on
   PRs going back through your history, and `ai_status` may change for some of them.

Leaving a rule deactivated costs nothing. Activating one you have not confirmed risks marking a colleague's
hand-written PR as machine-generated, which is much harder to undo than a missing signal.

## The stylometric rules

Five shipped rules look at *how the description is written* rather than at anything a tool stamps on it: an
assistant offering to revise its work, three or more bolded bullet lead-ins, a "Key changes" heading,
promotional adjectives ("comprehensive", "robust", "seamless"), and a first-person change report ("I've
refactored…"). Their names all begin with `Stylometry:`.

They are the weakest thing in the catalogue and they are treated accordingly: all five are `low`, disputed and
deactivated on seed. A match can only ever contribute to `ai_suspected`.

Before switching one on, dry-run it and read every match. These rules do not detect a tool — they detect a
writing style, and a colleague who writes careful, well-structured descriptions will match them constantly.
If more than a handful of the matches are people rather than machines, leave the rule off: on a page whose
readers are the matched developers' managers, that kind of false positive costs far more than a missed
signal.

## Structural signals: when the shape of the work is the evidence

Everything above matches *text* — a trailer, a file path, a login. A second family of rules reads the pull
request itself: how fast it arrived, how its commits are spaced, how many files it created, how quickly it
answered a review. Settings → **Structural signals**.

Five kinds read one pull request's own rows and run during a sync:

| Kind | Fires when |
|---|---|
| Large change delivered very fast | first commit to merge is implausibly short for the size |
| Burst of near-simultaneous substantial commits | several sizeable commits seconds apart |
| Whole change in one large commit | a large multi-file change with no intermediate state |
| Many files created across many directories | scaffolding, in one sitting |
| Repeated commits moments after review comments | not once, but three times over |

Four more read an author's own history, and four read the contents of the change itself — see **Author
baselines** and **Diff-level signals** below. Where a kind runs decides when it runs, and the dry-run panel
tells you so rather than reporting "no matches" for a rule it cannot preview.

**Two things these rules can never do.** They can never be high confidence — the database refuses it, not
just the form — so no combination of them ever marks a pull request as `AI explicit`. And one signal on its
own changes nothing: it takes two *distinct kinds* (`AI_SUSPECTED_MIN_STRUCTURAL_KINDS`) before a pull
request becomes `AI suspected`, and five commit bursts count as one kind, not five. A single signal is still
written, still shown, still exported — you are meant to read it, not act on it.

**They all arrive switched off, and that is not caution for its own sake.** A threshold has no right answer
across teams. A repository of generated API clients bursts commits all day. A team that squashes locally
before pushing trips "whole change in one commit" on every pull request. A new service legitimately creates
thirty files in one go. Run `manage.py seed_signal_rules`, then for each rule:

1. Open the **Dry run** panel, pick the kind, paste the thresholds, run it.
2. Read every match, not the count. Open two or three of the pull requests it names.
3. If most matches are people doing ordinary work, raise the threshold and run it again. If most are
   defensible either way, leave the rule off — this is a page your team's managers read.
4. Only when the matches look like the pattern you meant to catch, save and activate the rule.
5. Run `manage.py recompute` so stored pull requests are re-evaluated.

Re-tuning a threshold later is safe: the old signals are retired and rewritten with the new numbers, so no
pull request keeps a sentence quoting a threshold you no longer use.

**What they will never read.** Churn, reverts and follow-up fixes are outcomes PR Radar measures *for* the
AI cohort. They are deliberately not inputs to deciding who is in that cohort — otherwise the AI-quality
dashboards would be proving themselves. A test enforces it.

## Author baselines: judged against themselves, not against each other

Four of the structural kinds are different in a way worth understanding before you switch any of them on.
They do not ask "is this pull request unusual?" but "is this person's recent work unusual **for them**?":

| Kind | Fires when |
|---|---|
| Throughput jumped against the author's own history | their pull requests per week rose sharply — after the team's own change is divided out |
| Volume moved outside the author's usual hours | most recent volume landed in hours they did not previously commit in |
| Test-to-code ratio barely varies | the ratio holds nearly constant across a dozen pull requests |
| Descriptions broke from the author's own style | their PR descriptions changed length by a large factor, in either direction |

They are recomputed every night (`manage.py compute_baselines`, 01:00), not during a sync, because the answer
changes as more work arrives: somebody who looked unusual in March may look ordinary once April lands, and
the signal disappears on its own when that happens.

**What they refuse to do.** No baseline is computed for an author with fewer than `MIN_SAMPLE` pull requests
of history — a person with four pull requests has no baseline and the tool will not invent one. A team-wide
change fires nothing, because the throughput rule is normalised against the team over the same two windows.
"Usual hours" means the author's own hours, in your reporting timezone, so a night owl's baseline is their
own nights. An author who never writes tests does not trip the ratio rule. Bots get no baseline at all.

**What will trip them innocently.** A new project. A new team. A different kind of task. Coming back from
leave. Finishing something long-running. A move, a new timezone, a newborn, a switch to part-time. A changed
PR template — which will trip the description rule for your whole team at once, and is the first thing to
check before reading anything into it.

None of these is evidence of anything on its own. They are a reason to ask a person about their work, and
asking is a conversation you should be willing to have before you turn the rule on.

**They can change AI status on pull requests you already have.** One structural signal still changes nothing,
but a baseline signal plus a per-PR one is two distinct kinds — the default threshold for `AI suspected`.
After activating a baseline rule, run:

```
uv run python manage.py recompute --baselines
```

and expect some historical pull requests, and therefore some AI metrics, to move.

## Diff-level signals: reading the change itself

The rules above work from what GitHub tells PR Radar about a pull request. Four kinds need something more:
the actual lines of the change.

| Kind | Fires when |
|---|---|
| Large diff with almost no semantic change | the same diff counted again ignoring whitespace is a fraction of the size — a formatter's work |
| Comment density far above the code it joins | the added code carries far more comments and docstrings than the same files did before |
| The same block of code repeated across files | one eight-line block appears three times across at least two files |
| New dependency nothing in the diff uses | a manifest gains a package and no added line anywhere else mentions it |

**They cost no GitHub API calls.** They read the local bare clone the churn job already makes, so they run
inside the nightly churn run (`manage.py compute_churn`, 02:00) and add nothing to your rate limit.

**They are opt-in per repository**, because they read the contents of every change. In Settings →
**App settings**, add repositories to `DIFF_ANALYSIS_REPOSITORIES` as `owner/name`, one entry per repository,
or a single `*` for all of them. An empty list — the default — means no diff analysis runs anywhere.

Then:

1. Add the repository to `DIFF_ANALYSIS_REPOSITORIES`.
2. Activate the kinds you want in Settings → **Structural signals** (they ship off, like every structural rule).
3. Run `uv run python manage.py compute_churn` — or wait for the nightly run — and read the matches on the
   pull requests they name. `--no-diffs` runs churn without this half if you want to separate the two.
4. Adjust thresholds and re-run. As with every structural rule, a changed threshold retires the old signal
   and writes a new one, so no pull request keeps a sentence quoting a number you no longer use.

**What they will not do.** A repository with no usable clone that night produces no signals *and deletes
none* — a repository PR Radar could not reach is not evidence that a signal has gone away. A rebase-merged
pull request is skipped, exactly as it is for churn: there is no single commit representing the change. A
change with more files than `CHURN_MAX_FILES`, or a diff over two million characters, is left unanalysed
rather than read into memory. Lockfiles, vendored trees and generated code (`EXCLUDED_PATH_GLOBS`) are left
out of every measurement, and the comment-density kind also skips test files and prose.

**What will trip them innocently.** A deliberate "run the formatter over the repository" pull request. A
tricky module somebody documented properly. Boilerplate handlers, fixtures or migrations that legitimately
look alike. A dependency added in one pull request for use in the next. Read the pull request; the evidence
quotes both the measurement and the threshold so you can see which it is.

## What the repository page tells you instead

Some evidence is about the **repository**, not about any one pull request. If a repository has a `CLAUDE.md`,
an `.agents/` directory or a `.github/copilot-instructions.md`, somebody configured it for an agent — but that
is equally true of every pull request in it, so it is not a signal and it never changes a PR's AI status.

Each sync records it separately. Open a repository's page and read the **AI tooling** card:

- **a list of paths** — the agent configuration found at the tip of the default branch, at the time shown.
- **"No agent configuration was found"** — the repository was inspected and carries none.
- **"Not checked yet"** — nobody has looked. Not the same as the line above, and deliberately worded
  differently.

On the **Repositories** page, the filter drawer has an **AI tooling** select: *Configured for an agent* or
*No agent configuration*. Repositories nobody has probed yet appear under neither — an absence of evidence is
not evidence of absence. Which paths count is the `AI_TOOLING_PATH_GLOBS` setting (`docs/CONFIGURATION.md`);
add your team's own convention there if you have one.

## Fix a false positive

A rule that fires on text it shouldn't is a settings change, not a release. Open Settings → **Detection rules**,
find the rule (or search by its detector/tool), and either:

- **Edit** it — narrow the pattern, or lower its confidence so a shaky match no longer counts as `ai_explicit`.
- **Deactivate** it — its signals disappear from every PR the next time detection runs (the next sync, or the
  next `manage.py recompute`). A rule with existing signals can't be deleted outright — deactivating is the
  reversible equivalent.

## Dry-run a rule before saving it

On the same page, the **Dry run** panel lets you test a detector/pattern/tool/confidence combination against the
most recently stored pull requests — including one you haven't saved yet — before it can affect anyone's `ai_status`.
It reports how many of those PRs matched and shows the evidence for each; it never writes an `AISignal` row, so
running it repeatedly while you tune a pattern is free. An invalid regular expression is reported as a field
error right there instead of a failed save.

## Re-running detection over history

Detection rules only apply going forward automatically. To re-evaluate PRs you already have stored — after
editing a rule, adding a new one, or seeding the shipped rule set — run:

```
uv run python manage.py recompute --from 2026-01-01
```

`--repo owner/name` and `--project slug` narrow it further; with no filters it re-derives and re-detects every
stored PR. It makes no GitHub call, so it's safe to run as often as you like.
