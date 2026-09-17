from django import template

from apps.connections.check_codes import render_hint, render_message

register = template.Library()


@register.filter(name="render_checks")
def render_checks(last_check_result: dict) -> list[dict]:
    """last_check_result stores codes + params only (CLAUDE.md: never a rendered English
    message); this renders each stored check into the reader's language at read time."""
    rendered = []
    for check in (last_check_result or {}).get("checks", []):
        rendered.append(
            {
                "code": check["code"],
                "outcome": check["outcome"],
                "message": render_message(check["code"], check.get("params", {})),
                "hint": render_hint(check["code"], check.get("params", {})),
            }
        )
    return rendered
