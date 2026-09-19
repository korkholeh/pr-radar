"""T1: the shared `templates/partials/empty_state.html` contract and the `{% small_sample_note %}`
tag every `MetricResult` surface shares (plan §1/§2)."""

from __future__ import annotations

from django.template import Context, Template
from django.template.loader import render_to_string


def test_renders_title_and_explanation():
    html = render_to_string(
        "partials/empty_state.html", {"title": "No pull requests yet.", "explanation": "Run a sync."}
    )
    assert 'data-testid="empty-state"' in html
    assert "No pull requests yet." in html
    assert "Run a sync." in html


def test_omits_explanation_and_action_when_not_given():
    html = render_to_string("partials/empty_state.html", {"title": "No matching rows."})
    assert "No matching rows." in html
    assert "<a " not in html


def test_renders_action_link_when_given():
    html = render_to_string(
        "partials/empty_state.html",
        {"title": "Nothing synced yet.", "action_label": "Go to sync", "action_url": "/sync/"},
    )
    assert '<a href="/sync/"' in html
    assert "Go to sync" in html


def test_title_is_escaped():
    html = render_to_string("partials/empty_state.html", {"title": "<script>alert(1)</script>"})
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_small_sample_note_tag_carries_testid_and_label():
    html = Template("{% load dashboards %}{% small_sample_note %}").render(Context({}))
    assert 'data-testid="small-sample-note"' in html
    assert "Small sample" in html
