"""`PRFilters` — the PR list's filter state (plan §3), a member of `DashboardParams` rather than a
separate object threaded through every call site, so CSV, XLSX and the report reach exactly the
same filtered set as the on-screen table with no extra plumbing (CLAUDE.md: `DashboardParams` is
the single owner of query-string state)."""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import connection
from django.db.models import Q, QuerySet

from apps.activity.models import PullRequest
from apps.policy.models import PolicyViolation


@dataclass(frozen=True)
class PRFilters:
    author_ids: tuple[int, ...] = ()
    states: tuple[str, ...] = ()
    ai_statuses: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    size_buckets: tuple[str, ...] = ()
    has_violations: str = field(default="")  # "" | "yes" | "no"

    def is_empty(self) -> bool:
        return not (
            self.author_ids
            or self.states
            or self.ai_statuses
            or self.tools
            or self.size_buckets
            or self.has_violations
        )

    def apply(self, queryset: QuerySet[PullRequest]) -> QuerySet[PullRequest]:
        """The only place these filters touch the ORM, so `rows.pull_request_rows()` and every
        export/report call site narrow identically."""
        if self.author_ids:
            queryset = queryset.filter(author__person_id__in=self.author_ids)
        if self.states:
            queryset = queryset.filter(state__in=self.states)
        if self.ai_statuses:
            queryset = queryset.filter(ai_status__in=self.ai_statuses)
        if self.tools:
            # `ai_tools__contains=[tool]` needs JSON1 containment, which SQLite's
            # `supports_json_field_contains` reports as unavailable — but a `LIKE`-family lookup
            # against a real `jsonb` column (PostgreSQL, CLAUDE.md's `DATABASE_URL` target) does
            # not do containment either, so branch on the backend rather than picking one lookup
            # for both: SQLite matches the quoted string in the field's JSON text (exact, since
            # `Tool` values never nest as a substring of one another's JSON-quoted form); every
            # other backend uses real `__contains` (round 2 review MINOR).
            tool_query = Q()
            for tool in self.tools:
                if connection.features.supports_json_field_contains:
                    tool_query |= Q(ai_tools__contains=[tool])
                else:
                    tool_query |= Q(ai_tools__icontains=f'"{tool}"')
            queryset = queryset.filter(tool_query)
        if self.size_buckets:
            queryset = queryset.filter(size_bucket__in=self.size_buckets)
        if self.has_violations == "yes":
            queryset = queryset.filter(violations__status=PolicyViolation.Status.OPEN).distinct()
        elif self.has_violations == "no":
            queryset = queryset.exclude(violations__status=PolicyViolation.Status.OPEN).distinct()
        return queryset
