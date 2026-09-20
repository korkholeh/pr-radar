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
