"""Template-side access to `apps.policy.messages`: a violation's sentence is rendered at read
time from `(rule_code, details_params)`, never stored, so the console needs both at render."""

from django import template

from apps.policy.messages import render_violation
from apps.policy.messages import rule_label as _rule_label

register = template.Library()


@register.simple_tag
def render_violation_message(rule_code: str, details_params: dict) -> str:
    return render_violation(rule_code, details_params)


@register.filter
def rule_label(rule_code: str) -> str:
    return _rule_label(rule_code)
