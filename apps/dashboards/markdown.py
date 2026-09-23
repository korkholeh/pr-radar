"""Renders a pull request's Markdown body for the PR page.

The body is untrusted input written on GitHub, so two layers keep it inert: markdown-it runs with raw
HTML off (an HTML tag in the body shows as text), and nh3 then allows only the tags and attributes
this renderer produces, with http/https/mailto links only. Images become plain links: loading one
would call a third-party host from the reader's browser, and an attachment in a private repository
does not load without a GitHub session anyway.
"""

from __future__ import annotations

import re

import nh3
from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe
from markdown_it import MarkdownIt

_ALLOWED_TAGS = {
    "a", "blockquote", "br", "code", "del", "em", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "li",
    "ol", "p", "pre", "s", "strong", "table", "tbody", "td", "th", "thead", "tr", "ul",
}  # fmt: skip
_ALLOWED_ATTRIBUTES = {"a": {"href", "title"}, "ol": {"start"}}


def _render_image_as_link(self, tokens, idx, options, env) -> str:
    token = tokens[idx]
    label = self.renderInlineAsText(token.children or [], options, env) or token.attrGet("src") or ""
    return f'<a href="{escape(token.attrGet("src") or "")}">{escape(label)}</a>'


# GitHub task-list items ("- [ ] step", "- [x] step"), common in PR templates. A plain checkbox glyph,
# not an <input>: the list is read here, never ticked.
_TASK_ITEM_RE = re.compile(r"(<li>(?:\s*<p>)?)\[([ xX])\]\s")


def _task_item(match: re.Match[str]) -> str:
    return f"{match.group(1)}{'\u2611' if match.group(2) in 'xX' else '\u2610'} "


_md = MarkdownIt("commonmark", {"html": False, "linkify": True, "breaks": True}).enable(
    ["table", "strikethrough", "linkify"]
)
_md.add_render_rule("image", _render_image_as_link)


def render_markdown(text: str) -> SafeString:
    html = _TASK_ITEM_RE.sub(_task_item, _md.render(text))
    return mark_safe(  # noqa: S308 — sanitised by nh3 on the line it is built
        nh3.clean(
            html,
            tags=_ALLOWED_TAGS,
            attributes=_ALLOWED_ATTRIBUTES,
            url_schemes={"http", "https", "mailto"},
            link_rel="noopener noreferrer nofollow",
            set_tag_attribute_values={"a": {"target": "_blank"}},
        )
    )
