# 0009. Structural signals never reach high confidence

- **Status:** accepted
- **Date:** 2026-09-20

## Context

AI detection started with one rule family: **text rules** (`DetectionRule`). Each one quotes something a tool
actually wrote — a `Co-Authored-By: Claude` trailer, a Codex task link, an aider chat-history file in the diff.
The evidence is a substring of data GitHub returned, so a lead reading a flagged pull request sees the exact
text that flagged it and can agree or disagree in a second.

Phase 12 adds a second family: **structural signals** (`SignalRule`), in three groups — per-PR shape
(`structural.py`), author baselines (`baselines.py`) and diff-level heuristics (`diffsignals.py`). These quote
nothing. They say things like "this pull request landed nine commits inside eleven minutes", "this author's
weekly throughput is three times their own twelve-week median", "this diff reformats four hundred lines it does
not otherwise change". Each of those is a plausible sign of an agent at work. Each is also a plausible sign of a
developer rebasing, coming back from holiday, or running `ruff format` for the first time.

The stakes are not symmetric. `ai_status` drives the AI cohort, and the AI cohort drives every adoption number,
every AI-only policy check and the quality comparison a lead may take into a one-to-one. A wrong `ai_explicit`
on a hand-written pull request is an accusation that the author cannot see, cannot answer and did not earn.

This is expensive to reverse. `Confidence` is stored on `AISignal`, read by `resolve_ai_status`, referenced by
`DISCLOSURE_MISMATCH`, and written by every seeded rule. Letting a heuristic reach `high` once and retracting it
later means recomputing history and explaining to a lead why yesterday's numbers moved.

## Decision

**A structural signal is never `high` confidence, and the database enforces it.** `SignalRule` carries a
`CheckConstraint` rejecting `confidence="high"` at insert time, so neither the admin, nor a fixture, nor a
migration, nor a future code path can produce one. The form validates it too, but the form is not the guarantee.

**Two distinct structural kinds are required to move `ai_status`, and only as far as `ai_suspected`.**
`AI_SUSPECTED_MIN_STRUCTURAL_KINDS` (default 2) counts *kinds*, not signals: three thresholds of the same
heuristic firing is one observation seen three times, not three observations. No combination of structural
signals, in any number, ever produces `ai_explicit` — that status means "a tool said so in writing", and only a
text rule can establish it.

**Every structural signal shows its own arithmetic.** `evidence_code` plus `evidence_params` render as a full
sentence in the reader's language, naming the measurement and the threshold that produced it ("nine commits in
eleven minutes, against a threshold of six"). A lead disagreeing with a signal can see exactly which number to
change, and `AI_COHORT_INCLUDE_SUSPECTED` decides whether `ai_suspected` counts as AI at all.

**Outcome data is never an input.** `ChurnResult.churn_ratio`, `PullRequest.has_followup_fix` and
`is_revert` are measured *for* the AI cohort; feeding them back into deciding who is in it would make the
AI-quality dashboards self-proving. A test asserts no `SignalKind` reads them.

## Alternatives considered

- **Let a strong structural signal reach `high`** (say, a diff that is 95% one commit's wholesale reformat) —
  rejected: there is no threshold at which "this looks like a machine wrote it" becomes "a machine said it wrote
  it", and the cost of being wrong lands on a person who has no account here to answer with.
- **One structural kind is enough for `ai_suspected`** — rejected: every single kind has a benign explanation, and
  a single-kind rule would put most of an active team in the suspected cohort within a week.
- **Score the signals and threshold the score** — rejected as the primary mechanism: a weighted score hides which
  observation carried the decision, and the point of the evidence sentence is that a lead can argue with it.
  Composite scoring exists, but it counts distinct kinds rather than summing opaque weights.
- **Keep structural detection out of `ai_status` entirely and show it only as an annotation** — seriously
  considered. An installation that wants it sets `AI_SUSPECTED_MIN_STRUCTURAL_KINDS` above the number of kinds
  that exist, or leaves `AI_COHORT_INCLUDE_SUSPECTED` off so `ai_suspected` never enters the cohort the
  dashboards and the AI-only policy checks read. Rejected as the default because a team that adopts agents
  without disclosing them is exactly the case a lead most needs to see.

## Consequences

- **Buys:** an upper bound on how much damage a wrong heuristic can do. The worst case is `ai_suspected` on a
  hand-written PR, visible with its arithmetic, excludable by one setting, and outside the AI cohort entirely
  unless the installation opted in.
- **Costs:** an installation whose developers disclose nothing and whose tooling leaves no trailer will
  under-report AI adoption. That is the intended direction of the error.
- **Harder:** a new `SignalKind` cannot be introduced as a confident detector. It must define its evidence code,
  its parameters and its Ukrainian translation, and it must earn its place alongside a second kind before it
  changes anybody's status.
- **Revisit when:** a vendor ships a machine-readable provenance marker (a signed commit trailer, a GitHub API
  field) — at which point that marker is a *text* rule and may be `high`, and the structural family keeps its
  ceiling regardless.
