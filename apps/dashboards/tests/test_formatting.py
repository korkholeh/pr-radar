import json
from pathlib import Path

import pytest

from apps.dashboards.formatting import format_duration

FIXTURE = json.loads(
    (Path(__file__).resolve().parent.parent.parent.parent / "tests/fixtures/duration_cases.json").read_text()
)


@pytest.mark.parametrize("case", FIXTURE, ids=[str(c["seconds"]) for c in FIXTURE])
def test_format_duration_en(case):
    assert format_duration(case["seconds"], locale="en") == case["en"]


@pytest.mark.parametrize("case", FIXTURE, ids=[str(c["seconds"]) for c in FIXTURE])
def test_format_duration_uk(case):
    assert format_duration(case["seconds"], locale="uk") == case["uk"]


def test_none_renders_as_em_dash_never_zero():
    assert format_duration(None, locale="en") == "—"
    assert format_duration(None, locale="en") != "0"
