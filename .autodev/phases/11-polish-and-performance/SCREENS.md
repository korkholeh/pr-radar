# Phase 11 — Polish, performance and documentation · screenshots

Captured against the running `make e2e-up` surface (127.0.0.1:8100, `config.settings.e2e`) as the `e2e-lead`
persona, 1280×800, with a throwaway Playwright script (not committed). Data is entirely `manage.py seed_e2e`'s
existing small fixture set — no `--scale large` run was needed for these frames, since the phase's performance
work has no visual surface of its own (see *Not captured*).

## 01 · Page-level empty state

![Overview for a 2000-01-01..2000-01-07 period showing "No pull requests in this period" above the KPI grid](screenshots/01-empty-state.png)
A custom period with zero seeded PRs: the new shared empty-state partial explains the two reachable causes
("No pull requests match this period and filter. Widen the period or clear the filters.") while the filter bar
and every KPI card stay rendered underneath, exactly as designed — the page gains a sentence, it does not lose
content.
Evidences: deliverable "Empty states with an explanation on every page, chart and table" (PLAN.md §1, page level).

## 02 · MIN_SAMPLE greying on the Overview KPI grid

![Overview at the default 30-day period with most KPI cards dimmed and labelled "Small sample"](screenshots/02-small-sample.png)
The seeded e2e dataset is small enough that most metrics fall below `MIN_SAMPLE`: those cards render at reduced
opacity with an explicit "Small sample" text label under the value — never colour alone. "Open violations" and
"Open PRs", which do clear the threshold, render at full strength for contrast.
Evidences: deliverable "MIN_SAMPLE greying with the 'small sample' label everywhere a metric is shown".

## 03 · Dark theme, same Overview page

![The same 30-day Overview in dark mode, same KPI grid and small-sample labels](screenshots/03-dark-theme.png)
Same view as 02 with `data-theme="dark"` (theme switcher in the top-right), showing the retuned dark-mode tokens:
body text, muted text, the warning-coloured "Small sample" label and the KPI delta arrow all stay legible on the
dark surfaces.
Evidences: deliverable "A contrast audit of every token pair in both themes, with status never conveyed by
colour alone" — this frame is a spot check, not the audit itself, which is `tests/test_token_contrast.py`.

## 04 · Ukrainian UI on the People table

![People table in Ukrainian with wrapped, hyphenated column headers](screenshots/04-ukrainian-layout.png)
Language switched to `uk`, People page at the default 30-day period. Long Ukrainian headers ("Відкриті
порушення", "Час виконання (медіана)") wrap onto two lines with `hyphens-auto` instead of clipping, and the
per-row "≈" small-sample glyph in every metric cell carries the "Mала вибірка"-style tooltip text. **Also
visible, and worth flagging as-is rather than fixed here**: the `(Δ)`-suffixed delta columns ("PRs merged (Δ)",
"AI PR share (Δ)", …) are still in English — the base metric column translates but its delta variant does not,
a proofread gap this phase's `make messages` pass did not close.
Evidences: deliverable "Ukrainian proofread plus a long-string layout pass on KPI cards, buttons and column
headers" — the header-wrapping half holds; the untranslated `(Δ)` column labels are a residual gap, not a
capture artifact.

## Not captured

- `seed_demo --scale` at 50 repositories / 20 000 PRs and the resulting dashboard latency — a volume and a
  timing number, not something a static frame carries; it is pinned by `tests/test_performance.py` instead.
- The index/`select_related`/`prefetch_related` pass — an internal query-plan change with no visual surface.
- `docs/` completion — text files, not a screen.
- The full contrast-audit token×token table — asserted exhaustively by `tests/test_token_contrast.py`; frame 03
  is a representative spot check of it, not a substitute.
