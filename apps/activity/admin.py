from django.contrib import admin

from apps.activity.models import (
    CheckStatus,
    Commit,
    PRFile,
    PullRequest,
    PullRequestCommit,
    Review,
    ReviewComment,
)
from config.admin import ReadOnlyAdminMixin


@admin.register(PullRequest)
class PullRequestAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("__str__", "repository", "state", "author", "ai_status", "merged_at")
    list_filter = ("state", "is_draft", "ai_status")
    list_select_related = ("repository", "author")
    raw_id_fields = ("repository", "author", "merged_by", "reverts_pr")
    search_fields = ("title", "number")


@admin.register(Commit)
class CommitAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("sha", "repository", "committed_at")
    list_select_related = ("repository",)
    raw_id_fields = ("repository", "author_identity", "committer_identity")
    search_fields = ("sha",)


@admin.register(PullRequestCommit)
class PullRequestCommitAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("pull_request", "commit", "position")
    list_select_related = ("pull_request__repository", "commit")
    raw_id_fields = ("pull_request", "commit")


@admin.register(PRFile)
class PRFileAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("path", "pull_request", "is_test", "is_excluded")
    list_filter = ("is_test", "is_excluded")
    list_select_related = ("pull_request__repository",)
    raw_id_fields = ("pull_request", "matched_sensitive_rule")
    search_fields = ("path",)


@admin.register(Review)
class ReviewAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("pull_request", "reviewer", "state", "submitted_at")
    list_filter = ("state",)
    list_select_related = ("pull_request__repository", "reviewer")
    raw_id_fields = ("pull_request", "reviewer")


@admin.register(ReviewComment)
class ReviewCommentAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("pull_request", "author", "created_at", "is_review_thread")
    list_select_related = ("pull_request__repository", "author")
    raw_id_fields = ("pull_request", "author")


@admin.register(CheckStatus)
class CheckStatusAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("pull_request", "commit_sha", "rollup_state", "observed_at")
    list_filter = ("rollup_state",)
    list_select_related = ("pull_request__repository",)
    raw_id_fields = ("pull_request",)
