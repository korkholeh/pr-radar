from django import template

from apps.dashboards.formatting import format_duration as _format_duration

register = template.Library()


@register.filter(name="humanize_duration")
def humanize_duration(seconds: float | None) -> str:
    return _format_duration(seconds)
