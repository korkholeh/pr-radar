# Phase 10 — CI first-pass, follow-up fixes and churn · screenshots

Captured against the running `make e2e-up` surface (admin persona, English, light theme, 1280×800) with a
throwaway Playwright script (not committed). Data comes entirely from `manage.py seed_e2e`'s new `_seed_churn()`.

## 01 · Quality KPI row on the Overview dashboard
![Quality KPI row showing CI first-pass rate and 21-day churn](screenshots/01-overview-kpi.png)
The Quality row of the global Overview KPIs: Rework rate, CI first-pass rate, Revert rate, 21-day churn — the
last one reads 40.0% (both AI and Non-AI columns), greyed "Small sample" since only one `ChurnResult` row is
seeded (below `MIN_SAMPLE`=5). CI first-pass rate shows "—"/"Small sample" for the same reason — no
`CheckStatus` rows are seeded for this project, so this frame does not prove `ci_first_pass_rate`'s numeric path
(that is proven by `apps/metrics/tests/test_metrics_quality.py`, not a screenshot).
Evidences: "Churn surfaced ... in the churn KPI" (`churn_21d` now non-`None`), and that `ci_first_pass_rate` sits
in the same KPI row as the other quality metrics.

## 02 · PR detail page — a computed churn percentage
![PR detail page for a squash-merged PR showing a churn percentage sentence](screenshots/02-pr-detail-churn-ok.png)
`e2e-org/widget#950`'s Churn section: "40.0% churn — 6 of 10 lines still present after 21 days." — a full
sentence with named placeholders, not a bare percentage.
Evidences: "Churn surfaced on the PR detail page" (`ok` status branch), the `lines_at_merge`/`lines_surviving`
sentence from PLAN §10.

## 03 · PR detail page — rebase merge, unsupported
![PR detail page for a rebase-merged PR showing the unsupported-merge-method sentence](screenshots/03-pr-detail-churn-unsupported.png)
`e2e-org/widget#951`, merged by rebase: "Churn is not measured for rebase merges." — no percentage, no stray
"Churn not computed yet." text.
Evidences: the PR-commit-set-per-merge-method rule (`rebase` ⇒ `unsupported_merge_method`), rendered as its own
status branch on the PR detail page.

## 04 · Person page — the heuristic label on screen
![Person comparison table with the Follow-up fix rate row labelled heuristic](screenshots/04-person-followup-heuristic.png)
The Person page's Metric/Person/Project/Organization comparison table, scrolled to its last two rows: "21-day
churn" (40.0% at Project/Organization scope) and "Follow-up fix rate (heuristic)" — the metric's title carries
the label directly in the table, not in a tooltip or footnote.
Evidences: "The `followup_fix_rate` heuristic ... labelled a heuristic everywhere it is shown" and its visible
home in `PERSON_COMPARISON_METRIC_KEYS`.

## Not captured

- **The bare-clone lifecycle, `GIT_ASKPASS`, the git-blame algorithm and `compute_churn`/the nightly schedule**
  are correctly invisible to a screenshot — they are subprocess/filesystem/background-job work with no page of
  their own. Proven by `apps/churn/tests/*` and the PLAN's Verification table, not a picture.
- **`ci_first_pass_rate`'s numeric proof** (a first-CI-commit `SUCCESS` counted vs. `FAILURE` excluded from the
  numerator) — the e2e seed data has no `CheckStatus` rows, so the KPI shows "Small sample" rather than a number;
  the calculation itself is a metrics-registry test, not a UI state.
- **`too_large` and `error` churn statuses** on the PR detail page — deferred by the phase's own e2e plan
  (`e2e/plans/churn.plan.yaml`'s `deferred_not_authored`) to the non-browser test
  `apps/dashboards/tests/test_pull_request_detail.py`; no seeded PR reaches either state.
