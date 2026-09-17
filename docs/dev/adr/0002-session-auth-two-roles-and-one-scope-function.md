# 0002. Use Django session auth with two groups, and enforce all data isolation in a single `scope_for_user()`

- **Status:** accepted
- **Date:** 2026-09-17

## Context

Spec §11 defines the entire access model: users are leads and managers only; there is no self-registration; the
first admin comes from `createsuperuser`; there are two groups, `admin` and `lead`; and there is an optional
horizontal restriction, `UserProjectAccess`, where "no rows" means "sees everything" and "some rows" means "sees
only those projects, their repositories and the people who were active in them". The spec states outright that
this filtering must be "централізована в одному місці (`scope_for_user`), не в кожному view".

What makes this expensive to get wrong is the shape of the product: the same data leaves through pages, htmx
fragments, chart JSON endpoints, django-tables2 tables, CSV files and XLSX reports. Six exits, one rule.

Developers are not users at all (§1), so there is no self-service surface and no per-person login to design for.

## Decision

**Authentication.** Stock Django session auth against the default `User` model.
`LoginRequiredMiddleware` (Django 5.1+) is installed so the default for every URL is *denied*; login, logout and
password reset are marked `@login_not_required` explicitly. `Person` is never linked to `User`.

**Authorization, vertical.** Two groups created by a data migration:

- `admin` — holds the custom permission `catalog.manage_settings`, which gates connections, repository discovery,
  projects, identity mapping, policy, detection rules, `AppSetting`, users and sync configuration.
- `lead` — all dashboards, people pages, PR pages, violation status actions, and triggering a sync.

**Authorization, horizontal.** One function in `apps/accounts/scoping.py`:

```python
def scope_for_user(user) -> ScopeFilter
```

`ScopeFilter` exposes `projects()`, `repositories()`, `people()` and `pull_requests()` as querysets already
narrowed to what this user may see. Every selector, every chart endpoint, every table and every export **starts
from a `ScopeFilter`**, never from `Model.objects.all()`. Views receive it from a small mixin; services take it as
an argument. A user with no `UserProjectAccess` rows gets an unrestricted filter; a restricted user's "global"
level is the union of their projects, deduplicated by repository.

An out-of-scope project or repository id arriving in a query string is **dropped silently**, not answered with
403, so the response does not confirm that the id exists.

**Tests that hold the line**, per spec §12:

- a parametrized test that walks `urlpatterns` and asserts an anonymous request is redirected for *every* URL;
- a restricted lead cannot see another project's data in a page, in a chart JSON response, in a CSV export, or in
  an XLSX report — four assertions per fixture, because they are four different code paths.

## Alternatives considered

- **Per-view decorators or `PermissionRequiredMixin` only** — rejected: it protects the view and not the data.
  Exports and chart endpoints are precisely where a forgotten decorator is invisible until it leaks.
- **Row-level security in the database** — rejected: SQLite has none, and it would break ADR 0001's portability
  rule as well.
- **A custom `User` model with a `projects` M2M instead of `UserProjectAccess`** — rejected: swapping the user
  model later is painful, and the spec names the table.
- **Django Guardian / per-object permissions** — rejected: one object type, one rule, one dependency not worth
  taking.
- **Token or OAuth login against GitHub** — rejected: the tool must work with no GitHub credential at all (intake),
  and its users are not necessarily the token owners.

## Consequences

- **Buys:** one place to read when asking "can this user see this?", and one place to test. Adding a seventh
  export surface costs one `ScopeFilter` argument.
- **Costs:** every selector signature carries the filter. That verbosity is the point — a selector without one is
  visible in review and in `mypy`.
- **Harder:** ad-hoc queries in the shell or in a management command must decide explicitly whether they are
  system-level (unscoped, e.g. sync) or user-level (scoped). Management commands are system-level by definition
  and say so in a comment.
- **Revisit when:** a third role appears, or a restriction finer than "by project" is requested (by repository, by
  team). Both extend `ScopeFilter`; neither changes its callers.
