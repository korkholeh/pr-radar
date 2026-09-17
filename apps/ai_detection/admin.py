from django.contrib import admin

from apps.ai_detection.models import AISignal, DetectionRule
from config.admin import ReadOnlyAdminMixin


@admin.register(DetectionRule)
class DetectionRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "detector", "tool", "confidence", "is_active")
    list_filter = ("detector", "tool", "confidence", "is_active")
    search_fields = ("name", "pattern")


@admin.register(AISignal)
class AISignalAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("pull_request", "rule", "tool", "confidence", "detected_at")
    list_filter = ("tool", "confidence")
    list_select_related = ("pull_request__repository", "rule")
    search_fields = ("evidence",)
