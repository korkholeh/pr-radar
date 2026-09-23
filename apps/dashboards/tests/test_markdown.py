import pytest

from apps.dashboards.markdown import render_markdown


def test_renders_headings_emphasis_code_and_lists():
    html = render_markdown("## What\n\nUse `x` and **bold**.\n\n- one\n- two")
    assert "<h2>What</h2>" in html
    assert "<code>x</code>" in html
    assert "<strong>bold</strong>" in html
    assert "<li>one</li>" in html


def test_bare_url_becomes_a_link_that_opens_in_a_new_tab():
    html = render_markdown("See https://sentry.io/issues/1")
    assert 'href="https://sentry.io/issues/1"' in html
    assert 'target="_blank"' in html
    assert "noopener" in html


def test_tables_and_strikethrough_render():
    html = render_markdown("| a | b |\n|---|---|\n| 1 | 2 |\n\n~~gone~~")
    assert "<table>" in html
    assert "<td>1</td>" in html
    assert "<s>gone</s>" in html


def test_task_list_items_become_checkbox_glyphs():
    html = render_markdown("- [ ] todo\n- [x] done")
    assert "<li>☐ todo</li>" in html
    assert "<li>☑ done</li>" in html


def test_single_newline_is_a_line_break_as_on_github():
    assert "line1<br>" in render_markdown("line1\nline2")


@pytest.mark.parametrize(
    "body",
    [
        "<script>alert(1)</script>",
        '<img src="x" onerror="alert(1)">',
        '<a href="javascript:alert(1)">x</a>',
    ],
)
def test_raw_html_in_the_body_is_shown_as_text(body):
    html = render_markdown(body)
    assert "<script" not in html
    assert "<img" not in html
    assert "<a href" not in html


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,<b>x</b>", "vbscript:x"])
def test_unsafe_link_schemes_do_not_become_links(url):
    html = render_markdown(f"[click]({url})")
    assert "href=" not in html


def test_an_image_becomes_a_link_labelled_with_its_alt_text():
    html = render_markdown("![screenshot](https://github.com/user-attachments/assets/1.png)")
    assert "<img" not in html
    assert 'href="https://github.com/user-attachments/assets/1.png"' in html
    assert ">screenshot</a>" in html


def test_an_image_with_an_unsafe_url_is_not_linked():
    html = render_markdown("![x](javascript:alert(1))")
    assert "href=" not in html
