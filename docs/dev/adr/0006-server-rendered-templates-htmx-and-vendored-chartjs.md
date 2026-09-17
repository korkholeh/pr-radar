# 0006. Build the UI as server-rendered Django templates with htmx fragments and a vendored Chart.js — no SPA, no bundler

- **Status:** accepted
- **Date:** 2026-09-17

## Context

The UI is the product: dashboards at four scopes, day and period modes, KPI rows with deltas and sparklines, six
chart types, sortable filterable exportable tables, a PR detail page, a policy console and a settings area
(spec §10). It serves at most a handful of leads behind a login. Every filter must live in the query string so a
link is shareable (§10.1), pages must render in under 1.5 s (§14 phase 10), and everything must exist in English
and Ukrainian with correct plurals and locale-aware number, date and duration formats (§10.7).

Spec §2 names Django templates + htmx + optional Alpine.js, Chart.js **vendored in `static/` with no CDN**, and
`django-tables2` + `django-filter`. Intake settles CSS as the Tailwind standalone CLI with the compiled stylesheet
committed, explicitly so the application needs no Node build.

## Decision

**Server-rendered HTML is the default and the fallback.** Every dashboard URL renders a complete page from the
query string. htmx is used for *replacing regions*, never for constructing the page.

**One fragment convention, written down once:** a view that backs an htmx region answers the same URL with a full
page for a normal request and with the fragment when `HX-Request` is present, by choosing the template. Swap
targets and triggers are named explicitly in the template — never the default swap. This is what keeps the Back
button and shareable links working after several swaps.

**Errors are always visible.** An htmx endpoint never answers with an empty 400 body; it re-renders the form or an
error partial so the user sees something change. CSRF is configured once in `base.html` via `hx-headers`. Every
mutating action is POST/PUT/DELETE.

**Charts are data endpoints plus one JS module.** `/api/charts/<chart_key>?…` returns JSON; `static/js/charts.js`
holds all shared configuration — palette, duration and percentage formatters, tooltip and legend defaults — and
reads its colours from `getComputedStyle` so a theme change redraws rather than reloads (ADR 0008). Chart.js is
vendored as `static/vendor/chart.umd.js`, committed. No CDN: the tool must work on a plane, and a CDN is a third
party that would see which repositories a lead is looking at.

**No bundler, no npm in the run or test path.** Alpine.js, if used at all, is vendored the same way, for small
UI state (dropdowns, the theme menu) only. There is no application JavaScript build step.

**Tables and exports share their column definitions.** django-tables2 renders the UI table, and the CSV and XLSX
renderers read the same `ExportColumn` list, so what the user sees and what they download cannot drift
(spec §10.6). django-filter drives the filter forms.

**i18n is server-side by default:** `{% translate %}`/`{% blocktranslate %}` in templates, `gettext_lazy` in
Python, and `JavaScriptCatalog` for the strings `charts.js` needs. Duration formatting has exactly one
implementation in Python and one in JS, tested against each other.

## Alternatives considered

- **React/Vue SPA with a DRF API** — rejected: it adds a build toolchain, a second language, a public API surface
  to version and secure, a second i18n system and client-side routing — to serve dense tables and charts to five
  people. Every export and chart endpoint would become another place to forget `scope_for_user` (ADR 0002).
- **HTMX plus a JSON API for everything** — rejected: half the cost of an SPA for none of the benefit.
- **Chart.js from a CDN** — rejected by the spec and independently by privacy: offline operation and no third
  party learning the operator's browsing.
- **Plotly or ECharts** — rejected: heavier, and Chart.js covers all six chart shapes the spec lists.
- **Server-rendered chart images (matplotlib)** — rejected: the spec requires tooltips with exact values and live
  theme switching.
- **Pico.css instead of Tailwind** — rejected at intake: the dense tables and KPI cards need utility-level
  control (ADR 0008).
- **`django-tables2` only, with hand-written exports** — rejected: the spec requires that the exported view match
  the on-screen view exactly, including filters, sorting and search. One column list is the only way to guarantee
  that.

## Consequences

- **Buys:** one language, no bundler, no `node_modules` in the run path, pages that work with the browser's Back
  button, and authorization that cannot be bypassed by calling an API directly because there is no API.
- **Costs:** rich client-side interactions (drag-to-zoom on a chart, client-side table re-sorting of 20 000 rows)
  are not available. The spec does not ask for them; sorting and filtering are server-side and paginated.
- **Harder:** each interactive region needs a fragment template and a named target, which is more files than a
  component would be. The convention is documented in `docs/dev/architecture.md` so it is decided once.
- **Revisit when:** a screen genuinely needs coordinated client state across many regions, or when the audience
  grows past internal leads. Both would justify Alpine stores first and a framework only after that.
