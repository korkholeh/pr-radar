from django.contrib import admin

from apps.ai_detection.models import AISignal, DetectionRule, DiffAnalysis, SignalRule
from config.admin import ReadOnlyAdminMixin


@admin.register(DetectionRule)
class DetectionRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "detector", "tool", "confidence", "is_active")
    list_filter = ("detector", "tool", "confidence", "is_active")
    search_fields = ("name", "pattern")


@admin.register(SignalRule)
class SignalRuleAdmin(admin.ModelAdmin):
    """Editable here as well as in Settings, but `confidence = high` is refused by a database
    CheckConstraint either way — the invariant is not reachable by editing a row."""

    list_display = ("name", "kind", "tool", "confidence", "is_active")
    list_filter = ("kind", "tool", "confidence", "is_active")
    search_fields = ("name", "notes")


@admin.register(AISignal)
class AISignalAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("pull_request", "rule", "signal_rule", "tool", "confidence", "detected_at")
    list_filter = ("tool", "confidence")
    list_select_related = ("pull_request__repository", "rule", "signal_rule")
    search_fields = ("evidence", "evidence_code")


@admin.register(DiffAnalysis)
class DiffAnalysisAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """Computed by the churn run, so read-only here: re-running `manage.py compute_churn` is how a
    row changes, not an edit. Visible because `status`/`error` is the only place an operator can
    see why a repository's diffs are not producing signals."""

    list_display = ("pull_request", "status", "base_sha", "head_sha", "computed_at")
    list_filter = ("status",)
    list_select_related = ("pull_request__repository",)
    search_fields = ("pull_request__number", "error")
