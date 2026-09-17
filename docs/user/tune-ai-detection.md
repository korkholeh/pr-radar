# Tune AI detection

This page is for a lead or admin. PR Radar flags a PR's AI status (`ai_explicit`, `ai_disclosed`, `ai_suspected`,
`no_ai`, `unknown`) from two independent inputs: **signals** — regex rules matched against commits, the PR body
and metadata — and the PR's own **disclosure** (the "AI assistance" section of your PR template, if you use the
recommended one at `docs/pull_request_template.md`). If you don't see **Detection rules** in the top bar, ask an
admin for the `catalog.manage_settings` permission.

## Read a signal

Open a PR's page. The **AI signals** section lists every signal that matched: which detector found it (a commit
trailer, a commit author, the PR author, the PR body, an HTML comment, a label, the branch name or a commit
message), which tool it points to, its confidence and an evidence fragment — the actual text that matched, up to
200 characters, so you can judge a match without leaving the page. The resolved status is `ai_explicit` only when
at least one `high`-confidence signal fired; a `medium`/`low` signal alone yields `ai_suspected`.

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
