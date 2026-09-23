from __future__ import annotations

import datetime
from typing import Any
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import ngettext
from django.views.decorators.http import require_POST

from apps.accounts.selectors import ScopeFilter, scope_for_user
from apps.accounts.services import record_audit
from apps.catalog.services import get_int
from apps.metrics.timeframe import day_end_exclusive, day_start
from apps.metrics.timeframe import today as report_today
from apps.policy.forms import (
    AIPolicyForm,
    BulkViolationActionForm,
    SensitivePathRuleForm,
    ViolationFilterForm,
)
from apps.policy.messages import rule_label
from apps.policy.models import AIPolicy, PolicyViolation, SensitivePathRule
from apps.policy.selectors import (
    compliance_kpis,
    disclosure_mismatch_pull_requests,
    projects_in_scope,
    repositories_in_scope,
    violations_by_rule,
    violations_in_scope,
)
from apps.policy.services import (
    BulkStatusChangeResult,
    apply_bulk_status_change,
    current_policy,
    save_policy_version,
)
from config.htmx import is_htmx

SETTINGS_PERMISSION = "catalog.manage_settings"
MISMATCH_LIST_LIMIT = 20


def _period() -> tuple[datetime.date, datetime.date]:
    end = report_today()
    start = end - datetime.timedelta(days=get_int("DEFAULT_PERIOD_DAYS") - 1)
    return start, end


def _filter_form(request: HttpRequest, scope: ScopeFilter) -> ViolationFilterForm:
    form = ViolationFilterForm(
        request.GET, projects=projects_in_scope(scope), repositories=repositories_in_scope(scope)
    )
    form.is_valid()
    return form


def _filtered_violations(scope: ScopeFilter, cleaned: dict[str, Any]):
    queryset = (
        violations_in_scope(scope)
        .select_related("pull_request", "pull_request__repository", "resolved_by")
        .order_by("-created_at")
    )
    if cleaned.get("rule_code"):
        queryset = queryset.filter(rule_code=cleaned["rule_code"])
    if cleaned.get("severity"):
        queryset = queryset.filter(severity=cleaned["severity"])
    if cleaned.get("status"):
        queryset = queryset.filter(status__in=cleaned["status"])
    if cleaned.get("project"):
        queryset = queryset.filter(pull_request__repository__projects=cleaned["project"])
    if cleaned.get("repository"):
        queryset = queryset.filter(pull_request__repository=cleaned["repository"])
    if cleaned.get("date_from"):
        queryset = queryset.filter(created_at__gte=day_start(cleaned["date_from"]))
    if cleaned.get("date_to"):
        queryset = queryset.filter(created_at__lt=day_end_exclusive(cleaned["date_to"]))
    query = (cleaned.get("q") or "").strip()
    if query:
        text_match = Q(pull_request__title__icontains=query)
        if query.isdigit():
            text_match |= Q(pull_request__number=int(query))
        queryset = queryset.filter(text_match)
    return queryset


def _violations_table_context(
    request: HttpRequest, scope: ScopeFilter, filter_form: ViolationFilterForm
) -> dict:
    queryset = _filtered_violations(scope, filter_form.cleaned_data)
    page_size = get_int("VIOLATIONS_PAGE_SIZE")
    paginator = Paginator(queryset, page_size)
    page_obj = paginator.get_page(request.GET.get("page"))
    querystring = request.GET.copy()
    querystring.pop("page", None)
    return {"page_obj": page_obj, "querystring": querystring.urlencode()}


def _bulk_notice(result: BulkStatusChangeResult) -> str:
    if result.skipped_resolved:
        return ngettext(
            "Updated %(updated)s violation; skipped %(skipped)s resolved violation.",
            "Updated %(updated)s violations; skipped %(skipped)s resolved violations.",
            result.updated + result.skipped_resolved,
        ) % {"updated": result.updated, "skipped": result.skipped_resolved}
    return ngettext("Updated %(updated)s violation.", "Updated %(updated)s violations.", result.updated) % {
        "updated": result.updated
    }


def _console_page_context(
    request: HttpRequest, scope: ScopeFilter, filter_form: ViolationFilterForm, table_context: dict
) -> dict:
    """The full page's context, shared by `console`'s normal-request branch and
    `violation_bulk_action`'s non-htmx fallback (CLAUDE.md: the same URL returns a full page for a
    normal request and a fragment for an htmx one — the bulk-action URL is no exception)."""
    start, end = _period()
    kpis = compliance_kpis(scope, start, end)
    min_sample = get_int("MIN_SAMPLE")

    rule_counts = violations_by_rule(scope, start, end)
    max_count = max((row.count for row in rule_counts), default=0)
    chart_rows = [
        {
            "rule_code": row.rule_code,
            "label": rule_label(row.rule_code),
            "count": row.count,
            "open_count": row.open_count,
            "closed_count": row.count - row.open_count,
            "open_percent": round(row.open_count / max_count * 100) if max_count else 0,
            "closed_percent": round((row.count - row.open_count) / max_count * 100) if max_count else 0,
            # The chart counts every status over the period; the table defaults to open only and
            # no dates. The link opens the table on exactly what the bar counts.
            "table_query": urlencode(
                {
                    "rule_code": row.rule_code,
                    "status": PolicyViolation.Status.values,
                    "date_from": start.isoformat(),
                    "date_to": end.isoformat(),
                },
                doseq=True,
            ),
        }
        for row in rule_counts
    ]

    mismatch_prs = list(
        disclosure_mismatch_pull_requests(scope, start, end)
        .select_related("repository")
        .order_by("-created_at")[:MISMATCH_LIST_LIMIT]
    )

    return {
        **table_context,
        "filter_form": filter_form,
        "kpis": kpis,
        "min_sample": min_sample,
        "kpi_low_sample": kpis.sample_size < min_sample,
        "chart_rows": chart_rows,
        "mismatch_prs": mismatch_prs,
        "period_start": start,
        "period_end": end,
    }


@login_required
def console(request: HttpRequest) -> HttpResponse:
    scope = scope_for_user(request.user)
    filter_form = _filter_form(request, scope)
    table_context = _violations_table_context(request, scope, filter_form)

    if is_htmx(request):
        return render(request, "policy/partials/violations.html", {**table_context, "notice": None})

    context = {**_console_page_context(request, scope, filter_form, table_context), "notice": None}
    return render(request, "policy/console.html", context)


@login_required
@require_POST
def violation_bulk_action(request: HttpRequest) -> HttpResponse:
    scope = scope_for_user(request.user)
    action_form = BulkViolationActionForm(request.POST, violations_queryset=violations_in_scope(scope))

    notice = None
    if action_form.is_valid():
        violations = list(action_form.cleaned_data["violation_ids"])
        result = apply_bulk_status_change(
            request.user, violations, action_form.cleaned_data["action"], action_form.cleaned_data["comment"]
        )
        notice = _bulk_notice(result)

    filter_form = _filter_form(request, scope)
    table_context = _violations_table_context(request, scope, filter_form)
    action_errors = None if action_form.is_valid() else action_form.errors

    if is_htmx(request):
        return render(
            request,
            "policy/partials/violations.html",
            {**table_context, "notice": notice, "action_errors": action_errors},
        )

    context = {
        **_console_page_context(request, scope, filter_form, table_context),
        "notice": notice,
        "action_errors": action_errors,
    }
    return render(request, "policy/console.html", context)


@login_required
@permission_required(SETTINGS_PERMISSION, raise_exception=True)
def policy_settings(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = AIPolicyForm(request.POST)
        if form.is_valid():
            save_policy_version(request.user, form.cleaned_data)
            return redirect("policy:policy_settings")
    else:
        # Prefilled from the version in effect, so publishing a new version means changing what you
        # meant to change. With twenty-odd switches an empty form would quietly turn every one of
        # them off — and `save_policy_version` still writes a new row, never editing this one.
        form = AIPolicyForm(instance=current_policy())

    context = {"form": form, "history": list(AIPolicy.objects.all())}
    template = "policy/partials/policy_form.html" if is_htmx(request) else "policy/policy_settings.html"
    return render(request, template, context)


def _sensitive_paths_context() -> dict:
    return {"rules": list(SensitivePathRule.objects.select_related("project").order_by("project_id", "glob"))}


@login_required
@permission_required(SETTINGS_PERMISSION, raise_exception=True)
def sensitive_paths(request: HttpRequest) -> HttpResponse:
    template = (
        "policy/partials/sensitive_paths_content.html" if is_htmx(request) else "policy/sensitive_paths.html"
    )
    return render(request, template, _sensitive_paths_context())


def _render_sensitive_path_form(
    request: HttpRequest, form: SensitivePathRuleForm, rule: SensitivePathRule | None
) -> HttpResponse:
    template = (
        "policy/partials/sensitive_path_form.html" if is_htmx(request) else "policy/sensitive_path_form.html"
    )
    return render(request, template, {"form": form, "rule": rule})


def _rule_snapshot(rule: SensitivePathRule) -> dict[str, Any]:
    return {
        "project_id": rule.project_id,
        "glob": rule.glob,
        "ai_mode": rule.ai_mode,
        "description": rule.description,
        "is_active": rule.is_active,
    }


@login_required
@permission_required(SETTINGS_PERMISSION, raise_exception=True)
def sensitive_path_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = SensitivePathRuleForm(request.POST)
        if form.is_valid():
            rule = form.save()
            record_audit(request.user, "sensitive_path_rule.create", rule, after=_rule_snapshot(rule))
            return redirect("policy:sensitive_paths")
    else:
        form = SensitivePathRuleForm()
    return _render_sensitive_path_form(request, form, None)


@login_required
@permission_required(SETTINGS_PERMISSION, raise_exception=True)
def sensitive_path_edit(request: HttpRequest, pk: int) -> HttpResponse:
    rule = get_object_or_404(SensitivePathRule, pk=pk)
    if request.method == "POST":
        before = _rule_snapshot(rule)
        form = SensitivePathRuleForm(request.POST, instance=rule)
        if form.is_valid():
            form.save()
            record_audit(
                request.user, "sensitive_path_rule.update", rule, before=before, after=_rule_snapshot(rule)
            )
            return redirect("policy:sensitive_paths")
    else:
        form = SensitivePathRuleForm(instance=rule)
    return _render_sensitive_path_form(request, form, rule)


@login_required
@permission_required(SETTINGS_PERMISSION, raise_exception=True)
@require_POST
def sensitive_path_toggle(request: HttpRequest, pk: int) -> HttpResponse:
    rule = get_object_or_404(SensitivePathRule, pk=pk)
    before = rule.is_active
    rule.is_active = not rule.is_active
    rule.save(update_fields=["is_active"])
    record_audit(
        request.user,
        "sensitive_path_rule.toggle",
        rule,
        before={"is_active": before},
        after={"is_active": rule.is_active},
    )
    if is_htmx(request):
        return render(request, "policy/partials/sensitive_paths_content.html", _sensitive_paths_context())
    return redirect("policy:sensitive_paths")
