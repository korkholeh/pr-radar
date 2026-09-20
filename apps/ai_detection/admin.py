from django.contrib import admin

from apps.ai_detection.models import AISignal, DetectionRule, SignalRule
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
