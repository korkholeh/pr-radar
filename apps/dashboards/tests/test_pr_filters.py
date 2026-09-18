"""T9: `apps/dashboards/pr_filters.py::PRFilters` + `forms.PullRequestFilterForm` +
`DashboardParams.pr_filters` round trip through `params.parse()`/`to_query_dict()`."""

from __future__ import annotations

import datetime
from unittest import mock

import pytest
from django.http import QueryDict

from apps.activity.factories import PullRequestFactory
from apps.catalog.factories import IdentityFactory, PersonFactory, RepositoryFactory
from apps.dashboards.params import parse
from apps.dashboards.pr_filters import PRFilters

TODAY = datetime.date(2026, 6, 15)


def _parse(query: dict, **kwargs):
    qd = QueryDict(mutable=True)
    for key, value in query.items():
        if isinstance(value, (list, tuple)):
            qd.setlist(key, value)
        else:
            qd[key] = value
    return parse(qd, today=TODAY, **kwargs)


def test_empty_filters_is_empty():
    assert PRFilters().is_empty()


def test_non_empty_filters_is_not_empty():
    assert not PRFilters(states=("open",)).is_empty()
    assert not PRFilters(has_violations="yes").is_empty()


@pytest.mark.django_db
def test_query_string_round_trip():
    person = PersonFactory()
    identity = IdentityFactory(person=person)
    params = _parse(
        {
            "author": [str(person.pk)],
            "state": ["open", "merged"],
            "ai_status": ["ai_explicit"],
            "tool": ["cursor"],
            "size": ["S", "M"],
            "has_violations": "yes",
        },
        people=type(identity.person)._meta.default_manager.filter(pk=person.pk),
    )

    assert params.pr_filters.author_ids == (person.pk,)
    assert params.pr_filters.states == ("merged", "open")
    assert params.pr_filters.ai_statuses == ("ai_explicit",)
    assert params.pr_filters.tools == ("cursor",)
    assert params.pr_filters.size_buckets == ("M", "S")
    assert params.pr_filters.has_violations == "yes"

    query_dict = params.to_query_dict()
    reparsed = parse(query_dict, today=TODAY, people=type(person)._meta.default_manager.filter(pk=person.pk))
    assert reparsed.pr_filters == params.pr_filters


def test_unknown_enum_values_are_dropped():
    params = _parse({"state": ["open", "not-a-state"], "ai_status": ["bogus"]})
    assert params.pr_filters.states == ("open",)
    assert params.pr_filters.ai_statuses == ()


@pytest.mark.django_db
def test_out_of_scope_author_id_is_dropped():
    in_scope_person = PersonFactory()
    out_of_scope_person = PersonFactory()
    from apps.catalog.models import Person

    params = _parse(
        {"author": [str(out_of_scope_person.pk)]},
        people=Person.objects.filter(pk=in_scope_person.pk),
    )
    assert params.pr_filters.author_ids == ()


def test_empty_filters_serialise_to_nothing():
    params = _parse({})
    query_dict = params.to_query_dict()
    for key in ("author", "state", "ai_status", "tool", "size", "has_violations"):
        assert key not in query_dict


@pytest.mark.django_db
def test_apply_narrows_by_state_and_size():
    repository = RepositoryFactory()
    identity = IdentityFactory()
    open_pr = PullRequestFactory(
        repository=repository,
        author=identity,
        state="open",
        size_bucket="S",
        created_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC),
    )
    PullRequestFactory(
        repository=repository,
        author=identity,
        state="merged",
        size_bucket="L",
        created_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC),
    )
    from apps.activity.models import PullRequest

    filtered = PRFilters(states=("open",), size_buckets=("S",)).apply(PullRequest.objects.all())
    assert list(filtered) == [open_pr]


@pytest.mark.django_db
def test_apply_narrows_by_tool_on_sqlite_backend():
    """`ai_tools__contains` needs JSON1 containment support SQLite doesn't report — the filter
    must use a portable lookup (round 1 finding while building T10)."""
    repository = RepositoryFactory()
    identity = IdentityFactory()
    cursor_pr = PullRequestFactory(
        repository=repository,
        author=identity,
        ai_tools=["cursor"],
        created_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC),
    )
    PullRequestFactory(
        repository=repository,
        author=identity,
        ai_tools=["copilot"],
        created_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC),
    )
    from apps.activity.models import PullRequest

    filtered = PRFilters(tools=("cursor",)).apply(PullRequest.objects.all())
    assert list(filtered) == [cursor_pr]


@pytest.mark.django_db
def test_apply_uses_real_json_contains_when_the_backend_supports_it():
    """Round 2 review MINOR: `ai_tools__icontains` is a `LIKE`-family lookup, which does not do
    containment against a real `jsonb` column either (CLAUDE.md: ORM-only so `DATABASE_URL` can
    point at PostgreSQL later) — on a backend that reports containment support, the filter must use
    real `__contains`. Verified by inspecting the compiled SQL without executing it, since this
    checkout's own backend is SQLite and has no `JSON_CONTAINS` function to run against."""
    from apps.activity.models import PullRequest

    with mock.patch("apps.dashboards.pr_filters.connection.features.supports_json_field_contains", True):
        filtered = PRFilters(tools=("cursor",)).apply(PullRequest.objects.all())
        assert "JSON_CONTAINS" in str(filtered.query)
