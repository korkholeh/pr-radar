import datetime

import pytest
from freezegun import freeze_time

from apps.catalog.factories import ProjectFactory, RepositoryFactory
from apps.catalog.models import Project, Repository
from apps.dashboards.params import DashboardParams, auto_granularity, parse

TODAY = datetime.date(2026, 6, 15)  # a Monday


def _parse(query: dict, **kwargs):
    from django.http import QueryDict

    qd = QueryDict(mutable=True)
    for key, value in query.items():
        if isinstance(value, (list, tuple)):
            qd.setlist(key, value)
        else:
            qd[key] = value
    return parse(qd, today=TODAY, **kwargs)


@pytest.mark.parametrize(
    ("preset", "expected_from", "expected_to"),
    [
        ("7d", datetime.date(2026, 6, 9), TODAY),
        ("30d", datetime.date(2026, 5, 17), TODAY),
        ("90d", datetime.date(2026, 3, 18), TODAY),
        ("this_month", datetime.date(2026, 6, 1), TODAY),
        ("last_month", datetime.date(2026, 5, 1), datetime.date(2026, 5, 31)),
        ("quarter", datetime.date(2026, 4, 1), TODAY),
    ],
)
def test_preset_resolves_to_inclusive_range(preset, expected_from, expected_to):
    params = _parse({"preset": preset})
    assert (params.date_from, params.date_to) == (expected_from, expected_to)


def test_custom_preset_uses_from_and_to():
    params = _parse({"preset": "custom", "from": "2026-01-01", "to": "2026-01-10"})
    assert params.date_from == datetime.date(2026, 1, 1)
    assert params.date_to == datetime.date(2026, 1, 10)


def test_custom_preset_swaps_reversed_bounds():
    params = _parse({"preset": "custom", "from": "2026-01-10", "to": "2026-01-01"})
    assert params.date_from == datetime.date(2026, 1, 1)
    assert params.date_to == datetime.date(2026, 1, 10)


def test_custom_preset_without_bounds_falls_back_to_default():
    params = _parse({"preset": "custom"})
    assert params.preset == "custom"
    assert (params.date_from, params.date_to) == (datetime.date(2026, 5, 17), TODAY)


@pytest.mark.parametrize(
    ("days", "expected"),
    [(14, "day"), (15, "week"), (92, "week"), (93, "month")],
)
def test_auto_granularity_boundaries(days, expected):
    date_from = TODAY - datetime.timedelta(days=days - 1)
    assert auto_granularity(date_from, TODAY) == expected


def test_explicit_granularity_wins_over_auto():
    params = _parse({"preset": "90d", "granularity": "day"})
    assert params.granularity == "day"
    assert params.granularity_is_auto is False


def test_no_explicit_granularity_is_auto():
    params = _parse({"preset": "90d"})
    assert params.granularity == "week"
    assert params.granularity_is_auto is True


def test_malformed_date_falls_back_to_default_range():
    params = _parse({"preset": "custom", "from": "not-a-date", "to": "2026-01-10"})
    assert (params.date_from, params.date_to) == (datetime.date(2026, 5, 17), TODAY)


def test_unknown_preset_falls_back_to_default():
    params = _parse({"preset": "nonsense"})
    assert params.preset == "30d"
    assert (params.date_from, params.date_to) == (datetime.date(2026, 5, 17), TODAY)


def test_bad_cohort_falls_back_to_all():
    params = _parse({"cohort": "nonsense"})
    assert params.cohort == "all"


def test_bad_granularity_falls_back_to_auto():
    params = _parse({"preset": "90d", "granularity": "nonsense"})
    assert params.granularity == "week"
    assert params.granularity_is_auto is True


def test_bad_mode_falls_back_to_period():
    params = _parse({"mode": "nonsense"})
    assert params.mode == "period"


def test_negative_page_falls_back_to_default():
    params = _parse({"page": "-3"})
    assert params.page == 1


def test_non_numeric_page_falls_back_to_default():
    params = _parse({"page": "abc"})
    assert params.page == 1


@pytest.mark.django_db
def test_out_of_scope_project_id_is_dropped():
    in_scope = ProjectFactory()
    out_of_scope = ProjectFactory()
    params = _parse(
        {"project": [str(in_scope.pk), str(out_of_scope.pk)]},
        projects=Project.objects.filter(pk=in_scope.pk),
    )
    assert params.project_ids == (in_scope.pk,)


@pytest.mark.django_db
def test_out_of_scope_repository_id_is_dropped():
    in_scope = RepositoryFactory()
    out_of_scope = RepositoryFactory()
    params = _parse(
        {"repository": [str(in_scope.pk), str(out_of_scope.pk)]},
        repositories=Repository.objects.filter(pk=in_scope.pk),
    )
    assert params.repository_ids == (in_scope.pk,)


@pytest.mark.django_db
def test_query_string_round_trip():
    project = ProjectFactory()
    repository = RepositoryFactory()
    params = _parse(
        {
            "preset": "custom",
            "from": "2026-02-01",
            "to": "2026-02-20",
            "granularity": "week",
            "cohort": "compare",
            "project": [str(project.pk)],
            "repository": [str(repository.pk)],
            "q": "hello",
            "sort": "-name",
            "page": "3",
            "table": "projects",
        },
        projects=Project.objects.filter(pk=project.pk),
        repositories=Repository.objects.filter(pk=repository.pk),
    )
    round_tripped = parse(
        params.to_query_dict(),
        projects=Project.objects.filter(pk=project.pk),
        repositories=Repository.objects.filter(pk=repository.pk),
        today=TODAY,
    )
    assert round_tripped == params


@freeze_time("2026-06-15T12:00:00+03:00")
def test_preset_change_changes_the_query_string():
    week = parse_default_query({"preset": "7d"})
    month = parse_default_query({"preset": "90d"})
    assert week.to_query_dict().urlencode() != month.to_query_dict().urlencode()


def parse_default_query(query: dict) -> DashboardParams:
    from django.http import QueryDict

    qd = QueryDict(mutable=True)
    for key, value in query.items():
        qd[key] = value
    return parse(qd)


# -- the repositories table's AI-tooling filter (phase 12, stage 3) ------------------------------


@pytest.mark.parametrize("value", ["yes", "no"])
def test_ai_tooling_filter_round_trips_through_the_query_string(value):
    params = _parse({"ai_tooling": value})
    assert params.ai_tooling == value
    assert params.to_query_dict()["ai_tooling"] == value


def test_unset_ai_tooling_filter_is_omitted_from_the_query_string():
    params = _parse({})
    assert params.ai_tooling == ""
    assert "ai_tooling" not in params.to_query_dict()


def test_unknown_ai_tooling_value_falls_back_to_unfiltered():
    """Same contract as every other filter: a hand-edited URL narrows nothing rather than
    raising (RISKS row 3)."""
    assert _parse({"ai_tooling": "maybe"}).ai_tooling == ""
