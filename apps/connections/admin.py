from django.contrib import admin

from apps.connections.models import GitHubConnection


@admin.register(GitHubConnection)
class GitHubConnectionAdmin(admin.ModelAdmin):
    exclude = ("token_encrypted", "private_key_encrypted")
    readonly_fields = ("token_last4", "token_login")
    list_display = ("name", "kind", "status", "is_active", "last_checked_at")
    list_filter = ("kind", "status", "is_active")
    search_fields = ("name", "owner_login")

    def has_add_permission(self, request):
        return False
