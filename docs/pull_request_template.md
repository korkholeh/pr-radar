# Recommended PR template

Copy this into `.github/pull_request_template.md` of a repository PR Radar tracks. It matches the
defaults the disclosure parser (`apps/ai_detection/disclosure.py`) ships with — the heading, the
three checkbox labels and the tools line are read verbatim by `DISCLOSURE_SECTION_HEADINGS`,
`DISCLOSURE_LABELS_NONE` / `_PARTIAL` / `_SUBSTANTIAL` and `DISCLOSURE_TOOLS_LABELS`
(`docs/CONFIGURATION.md`). Changing the wording here without updating those settings makes the
parser stop recognising it.

```markdown
## Description

<!-- What does this change do, and why? -->

### AI assistance

- [ ] None — no AI tool was used to write this change
- [ ] Partial — AI suggested snippets, autocomplete, or was used for review
- [ ] Substantial — most of the diff was AI-generated

### AI tools used

<!-- e.g. Claude Code, Copilot. Leave blank if none. -->
```

Tick exactly one box. Two ticks are read as "ambiguous" (the author did not answer the question),
and a PR opened from a fork or an older template with no such section is read as "missing" —
neither is treated as "no AI was used".

`tests/test_docs.py::test_pull_request_template_parses_as_the_parser_expects` asserts the shipped
template parses to `missing` with all boxes unticked and to `substantial` with only the third
ticked, so this file and the parser can never drift silently.
