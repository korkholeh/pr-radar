"""T21: acceptance criterion #8 — a repository belonging to two projects is counted once at the
global level, in full at each project's level (plan §"What exists": `_grouped_value()` already
derives PROJECT values from REPO values in Python; this proves it end to end at the page level via
`metrics.compute()`, the same call `kpis.build_kpi_row()`/`services.build_dashboard()` make)."""

from __future__ import annotations

import datetime

import pytest

from apps.accounts.selectors import ScopeFilter
from apps.activity.factories import PullRequestFactory
from apps.catalog.factories import IdentityFactory, PersonFactory, ProjectFactory, RepositoryFactory
from apps.metrics.models import ScopeType
from apps.metrics.rollups import rebuild
from apps.metrics.services import compute
from apps.metrics.types import Scope

DATE_FROM = datetime.date(2026, 8, 1)
DATE_TO = datetime.date(2026, 8, 31)


@pytest.mark.django_db
def test_shared_repository_is_counted_once_globally():
    repository = RepositoryFactory()
    project_a = ProjectFactory()
    project_b = ProjectFactory()
    project_a.repositories.add(repository)
    project_b.repositories.add(repository)
    author = IdentityFactory(person=PersonFactory())

    for offset in range(3):
        PullRequestFactory(
            repository=repository,
            author=author,
            state="merged",
            created_at=datetime.datetime(2026, 8, 5 + offset, tzinfo=datetime.UTC),
            merged_at=datetime.datetime(2026, 8, 10 + offset, tzinfo=datetime.UTC),
        )

    rebuild(DATE_FROM, DATE_TO)

    access = ScopeFilter(unrestricted=True)
    global_scope = Scope(scope_type=ScopeType.GLOBAL, scope_id=None, access=access)
    project_a_scope = Scope(scope_type=ScopeType.PROJECT, scope_id=project_a.id, access=access)
    project_b_scope = Scope(scope_type=ScopeType.PROJECT, scope_id=project_b.id, access=access)

    global_value = compute(["prs_merged"], global_scope, DATE_FROM, DATE_TO)["prs_merged"].value
    project_a_value = compute(["prs_merged"], project_a_scope, DATE_FROM, DATE_TO)["prs_merged"].value
    project_b_value = compute(["prs_merged"], project_b_scope, DATE_FROM, DATE_TO)["prs_merged"].value

    assert global_value == 3
    assert project_a_value == 3
    assert project_b_value == 3
    assert global_value < project_a_value + project_b_value
