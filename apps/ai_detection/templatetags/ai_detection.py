"""Rendering a stored structural signal into a sentence at read time.

The template cannot call `render_evidence` itself — a filter takes one argument and this needs a
code plus its parameter dict — so it goes through a tag. The rendering rule is the project's
standing one: system-generated text is stored as a code plus parameters and turned into prose in
the reader's language, never stored as English (CLAUDE.md).
"""

from django import template

from apps.ai_detection.evidence import render_evidence

register = template.Library()


@register.simple_tag(name="signal_evidence")
def signal_evidence(evidence_code: str, evidence_params: dict | None = None) -> str:
    return render_evidence(evidence_code, evidence_params)
