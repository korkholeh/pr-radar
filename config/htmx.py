"""The one predicate every fragment-vs-full-page view uses (ADR 0006): a view answers the
same URL with a full page for a normal request and a fragment when htmx asks for a swap."""

from django.http import HttpRequest


def is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"
