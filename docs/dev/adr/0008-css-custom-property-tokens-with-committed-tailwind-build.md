# 0008. Define all colour as CSS custom-property tokens under `data-theme`, and commit the Tailwind standalone-CLI output

- **Status:** accepted
- **Date:** 2026-09-17

## Context

Spec §10.5 is unusually specific, because theming here is a correctness requirement rather than a preference:
three modes (System / Light / Dark) with the choice stored **server-side** on `UserPreference` and mirrored to a
cookie; `data-theme` set on `<html>` by the server so there is no flash; **colour only through design tokens**
(`--bg`, `--surface`, `--surface-2`, `--border`, `--text`, `--text-muted`, `--accent`, `--good`, `--bad`,
`--neutral`, `--warning`, `--series-ai`, `--series-non-ai`, `--series-1..8`, `--grid`, `--tooltip-bg`), with a
test that greps for hard-coded `#hex`/`rgb(` outside the token file; charts reading their colours from
`getComputedStyle` and redrawing on theme change; WCAG 2.1 AA contrast in *both* themes; status never conveyed by
colour alone; and coverage of tables, forms, badges, modals, empty states, the date picker, the review heat map
and the 403/404/500 pages.

Intake settles the CSS tool: **Tailwind standalone CLI**, compiled CSS committed, no Node build for the
application.

This is expensive to reverse: the token names appear in every template, in `charts.js`, and in the grep test.
Changing the mechanism later means touching every file that has a colour.

## Decision

**The token file is the single source of colour.** `static/css/tokens.css` defines every token twice — once for
`[data-theme="light"]`, once for `[data-theme="dark"]`. Nothing else in the project — no template, no JS, no
component CSS — may contain a hex or `rgb()` literal. A test greps for it and fails the build.

**Tailwind consumes the tokens, it does not replace them.** The Tailwind entry point imports `tokens.css` and maps
utility colours onto the same variables, so `bg-surface` and `var(--surface)` are the same value by construction,
and the dark variant is bound to the attribute rather than to `prefers-color-scheme`:

- Tailwind v4: `@custom-variant dark (&:where([data-theme="dark"], [data-theme="dark"] *));` plus an
  `@theme inline { --color-surface: var(--surface); … }` block, which is exactly what `inline` exists for.
- Tailwind v3: `darkMode: ['selector', '[data-theme="dark"]']` and a `colors` map of `var(--…)` entries.

The run pins one Tailwind major version in `Makefile` and `docs/SETUP.md`. Either satisfies the spec; the mapping
above is the part that must be right.

**The build is out of band, the output is in the repository.** `make css` downloads the standalone binary into a
git-ignored `.tools/` and writes `static/css/app.css`, which is **committed**. Consequently `uv sync`,
`pytest`, `runserver` and the e2e suite never need Node, a network fetch, or a watcher. A test asserts that
`app.css` contains every token name, so a stale commit of the stylesheet is caught rather than shipped.

**Theme resolution, in order:** `UserPreference.theme` for a logged-in user → the `theme` cookie (which covers the
login page and gives an instant first paint) → `system`. The server writes `data-theme` on `<html>` during
rendering; for `system` a tiny inline script in `<head>` resolves `prefers-color-scheme` before the stylesheet is
applied. This is the only inline script in the project, and it exists solely to prevent the flash.

**Charts follow the tokens.** `charts.js` reads series, grid, axis, legend and tooltip colours via
`getComputedStyle(document.documentElement)` and redraws every chart on a `themechange` event and on a
`matchMedia` change in system mode. AI and non-AI keep the same identity in both themes, with values tuned per
background rather than inverted.

**Accessibility is part of the token definition**, not a later pass: text ≥ 4.5:1 and KPI numbers and chart
elements ≥ 3:1 against their own surface, in both themes; a colour-blind-safe categorical ramp; and ▲▼ arrows,
icons and text badges so that no status is colour-only. Dark is not an inversion — the background sits around
`#0f1115`–`#161a20`, surfaces are lighter than the background, and large areas avoid pure white text.

**Print and PNG chart export are always light**, regardless of the UI theme.

## Alternatives considered

- **Tailwind's built-in `dark:` with `prefers-color-scheme`** — rejected: it cannot express a stored three-state
  preference (System / Light / Dark) that follows the user across devices.
- **`class="dark"` instead of `data-theme`** — rejected: the spec names `data-theme`, and an attribute leaves room
  for a third theme without overloading the class list.
- **Client-side theme application from `localStorage`** — rejected: the preference must be server-side so it
  follows the user to another device, and a client-applied theme flashes.
- **Two compiled stylesheets, one per theme** — rejected: doubles the build, and charts still need runtime colour
  reading; one stylesheet with two token blocks is strictly simpler.
- **Pico.css** — rejected at intake: the dense tables, KPI cards and heat map need utility-level control.
- **Tailwind via npm/PostCSS with a Node build** — rejected at intake: it would put `node_modules` in the run and
  test path of a Python project for one CSS file.
- **Committing the Tailwind binary itself** — rejected: a platform-specific ~100 MB artefact in a repository that
  must stay portable. Downloading it on demand into `.tools/` is a build-machine concern.
- **Hand-written CSS with no framework** — rejected: it would satisfy the token rule trivially but cost far more
  markup discipline across the number of screens in §10.3.

## Consequences

- **Buys:** a theme switch that is one attribute, a grep test that makes drift impossible, charts that match the
  page without duplicated palettes, and a repository that clones and runs with only Python installed.
- **Costs:** `static/css/app.css` is a generated file under version control, so it can be forgotten after a
  template change. The token-presence test and a `make css` step in `docs/SETUP.md` are the mitigation; a missed
  rebuild shows as a missing utility class, not as a wrong colour.
- **Harder:** every new UI element must pick an existing token or add one to `tokens.css` with both theme values
  and a checked contrast ratio. That friction is the intent.
- **Revisit when:** a third theme (high contrast) is requested — which the attribute already allows — or if the
  standalone binary stops being published for the target platform, at which point the same input files build under
  npm without changing a single template.
