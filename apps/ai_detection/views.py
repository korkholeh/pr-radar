from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.selectors import scope_for_user
from apps.accounts.services import record_audit
from apps.ai_detection.forms import (
    DetectionRuleForm,
    DryRunForm,
    SignalDryRunForm,
    SignalRuleForm,
)
from apps.ai_detection.models import SIGNAL_KIND_FAMILY, DetectionRule, SignalFamily, SignalRule
from apps.ai_detection.services import dry_run_rule, dry_run_signal_rule
from apps.catalog.services import get_int
from config.htmx import is_htmx

PERMISSION = "catalog.manage_settings"


def _rules_context() -> dict:
    return {"rules": list(DetectionRule.objects.order_by("detector", "name"))}


@login_required
@permission_required(PERMISSION, raise_exception=True)
def rules_list(request: HttpRequest) -> HttpResponse:
    template = "ai_detection/partials/rules_content.html" if is_htmx(request) else "ai_detection/rules.html"
    return render(request, template, {**_rules_context(), "dry_run_form": DryRunForm()})


def _render_rule_form(
    request: HttpRequest, form: DetectionRuleForm, rule: DetectionRule | None
) -> HttpResponse:
    template = "ai_detection/partials/rule_form.html" if is_htmx(request) else "ai_detection/rule_form.html"
    return render(request, template, {"form": form, "rule": rule})


@login_required
@permission_required(PERMISSION, raise_exception=True)
def rule_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = DetectionRuleForm(request.POST)
        if form.is_valid():
            rule = form.save()
            record_audit(request.user, "detection_rule.create", rule, after=form.cleaned_data)
            return redirect("ai_detection:rules")
    else:
        form = DetectionRuleForm()
    return _render_rule_form(request, form, None)


@login_required
@permission_required(PERMISSION, raise_exception=True)
def rule_edit(request: HttpRequest, pk: int) -> HttpResponse:
    rule = get_object_or_404(DetectionRule, pk=pk)
    if request.method == "POST":
        before = {field: getattr(rule, field) for field in DetectionRuleForm.Meta.fields}
        form = DetectionRuleForm(request.POST, instance=rule)
        if form.is_valid():
            form.save()
            record_audit(request.user, "detection_rule.update", rule, before=before, after=form.cleaned_data)
            return redirect("ai_detection:rules")
    else:
        form = DetectionRuleForm(instance=rule)
    return _render_rule_form(request, form, rule)


@login_required
@permission_required(PERMISSION, raise_exception=True)
@require_POST
def rule_toggle(request: HttpRequest, pk: int) -> HttpResponse:
    rule = get_object_or_404(DetectionRule, pk=pk)
    before = rule.is_active
    rule.is_active = not rule.is_active
    rule.save(update_fields=["is_active"])
    record_audit(
        request.user,
        "detection_rule.toggle",
        rule,
        before={"is_active": before},
        after={"is_active": rule.is_active},
    )
    if is_htmx(request):
        return render(request, "ai_detection/partials/rules_content.html", _rules_context())
    return redirect("ai_detection:rules")


@login_required
@permission_required(PERMISSION, raise_exception=True)
@require_POST
def rule_dry_run(request: HttpRequest) -> HttpResponse:
    form = DryRunForm(request.POST)
    context: dict = {"dry_run_form": form}
    if form.is_valid():
        unsaved_rule = form.save(commit=False)
        scope = scope_for_user(request.user)
        limit = get_int("DETECTION_DRY_RUN_PR_COUNT")
        try:
            # DryRunForm.clean_pattern already rejects a non-compiling regex; this is a
            # defensive net, not the primary gate.
            matches = dry_run_rule(unsaved_rule, scope, limit)
        except ValueError as exc:
            form.add_error("pattern", str(exc))
        else:
            context["matches"] = matches
            context["limit"] = limit
    return render(request, "ai_detection/partials/dry_run_result.html", context)


# --- Structural signals (phase 12, stage 4) -----------------------------------------------------
#
# Deliberately a page of its own rather than a second table on Detection rules: the two families
# are edited differently (a regex versus a JSON threshold dict), read differently, and a lead
# tuning thresholds is doing a different job from one confirming a vendor's marker.


def _signal_rules_context() -> dict:
    return {"signal_rules": list(SignalRule.objects.order_by("kind", "name"))}


@login_required
@permission_required(PERMISSION, raise_exception=True)
def signal_rules_list(request: HttpRequest) -> HttpResponse:
    template = (
        "ai_detection/partials/signal_rules_content.html"
        if is_htmx(request)
        else "ai_detection/signal_rules.html"
    )
    return render(request, template, {**_signal_rules_context(), "dry_run_form": SignalDryRunForm()})


def _render_signal_rule_form(
    request: HttpRequest, form: SignalRuleForm, rule: SignalRule | None
) -> HttpResponse:
    template = (
        "ai_detection/partials/signal_rule_form.html"
        if is_htmx(request)
        else "ai_detection/signal_rule_form.html"
    )
    return render(request, template, {"form": form, "rule": rule})


@login_required
@permission_required(PERMISSION, raise_exception=True)
def signal_rule_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = SignalRuleForm(request.POST)
        if form.is_valid():
            rule = form.save()
            record_audit(request.user, "signal_rule.create", rule, after=form.cleaned_data)
            return redirect("ai_detection:signal_rules")
    else:
        form = SignalRuleForm()
    return _render_signal_rule_form(request, form, None)


@login_required
@permission_required(PERMISSION, raise_exception=True)
def signal_rule_edit(request: HttpRequest, pk: int) -> HttpResponse:
    rule = get_object_or_404(SignalRule, pk=pk)
    if request.method == "POST":
        before = {field: getattr(rule, field) for field in SignalRuleForm.Meta.fields}
        form = SignalRuleForm(request.POST, instance=rule)
        if form.is_valid():
            form.save()
            record_audit(request.user, "signal_rule.update", rule, before=before, after=form.cleaned_data)
            return redirect("ai_detection:signal_rules")
    else:
        form = SignalRuleForm(instance=rule)
    return _render_signal_rule_form(request, form, rule)


@login_required
@permission_required(PERMISSION, raise_exception=True)
@require_POST
def signal_rule_toggle(request: HttpRequest, pk: int) -> HttpResponse:
    rule = get_object_or_404(SignalRule, pk=pk)
    before = rule.is_active
    rule.is_active = not rule.is_active
    rule.save(update_fields=["is_active"])
    record_audit(
        request.user,
        "signal_rule.toggle",
        rule,
        before={"is_active": before},
        after={"is_active": rule.is_active},
    )
    if is_htmx(request):
        return render(request, "ai_detection/partials/signal_rules_content.html", _signal_rules_context())
    return redirect("ai_detection:signal_rules")


@login_required
@permission_required(PERMISSION, raise_exception=True)
@require_POST
def signal_rule_dry_run(request: HttpRequest) -> HttpResponse:
    form = SignalDryRunForm(request.POST)
    context: dict = {"dry_run_form": form}
    if form.is_valid():
        unsaved_rule = form.save(commit=False)
        family = SIGNAL_KIND_FAMILY.get(unsaved_rule.kind)
        if family == SignalFamily.PER_PR:
            scope = scope_for_user(request.user)
            limit = get_int("DETECTION_DRY_RUN_PR_COUNT")
            context["matches"] = dry_run_signal_rule(unsaved_rule, scope, limit)
            context["limit"] = limit
        else:
            # A baseline kind reads an author's whole history and a diff kind reads the bytes of a
            # change from the local clone. Neither input is available in a request, and reporting
            # "no matches" for a rule that was never actually run would be a lie the reader has no
            # way to catch.
            context["unsupported_family"] = family
    return render(request, "ai_detection/partials/signal_dry_run_result.html", context)
