from django.contrib import admin

from apps.policy.models import AIPolicy, PolicyViolation, SensitivePathRule


@admin.register(AIPolicy)
class AIPolicyAdmin(admin.ModelAdmin):
    list_display = ("effective_from", "require_disclosure", "require_human_approval", "min_human_approvals")
    list_filter = ("require_disclosure", "require_human_approval")


@admin.register(SensitivePathRule)
class SensitivePathRuleAdmin(admin.ModelAdmin):
    list_display = ("glob", "project", "ai_mode", "is_active")
    list_filter = ("ai_mode", "is_active")
    list_select_related = ("project",)
    raw_id_fields = ("project",)
    search_fields = ("glob",)


@admin.register(PolicyViolation)
class PolicyViolationAdmin(admin.ModelAdmin):
    list_display = ("pull_request", "rule_code", "severity", "status", "created_at")
    list_filter = ("rule_code", "severity", "status")
    list_select_related = ("pull_request__repository",)
    raw_id_fields = ("pull_request", "resolved_by")
