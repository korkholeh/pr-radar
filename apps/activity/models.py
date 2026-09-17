from django.db import models
from django.utils.translation import gettext_lazy as _


class AIStatus(models.TextChoices):
    AI_EXPLICIT = "ai_explicit", _("AI explicit")
    AI_DISCLOSED = "ai_disclosed", _("AI disclosed")
    AI_SUSPECTED = "ai_suspected", _("AI suspected")
    NO_AI = "no_ai", _("No AI")
    UNKNOWN = "unknown", _("Unknown")


class AIDisclosure(models.TextChoices):
    NONE = "none", _("None")
    PARTIAL = "partial", _("Partial")
    SUBSTANTIAL = "substantial", _("Substantial")
    MISSING = "missing", _("Missing")
    AMBIGUOUS = "ambiguous", _("Ambiguous")


class SizeBucket(models.TextChoices):
    XS = "XS", _("XS")
    S = "S", _("S")
    M = "M", _("M")
    L = "L", _("L")
    XL = "XL", _("XL")


class PullRequest(models.Model):
    class State(models.TextChoices):
        OPEN = "open", _("Open")
        CLOSED = "closed", _("Closed")
        MERGED = "merged", _("Merged")

    class MergeMethod(models.TextChoices):
        MERGE = "merge", _("Merge")
        SQUASH = "squash", _("Squash")
        REBASE = "rebase", _("Rebase")
        UNKNOWN = "unknown", _("Unknown")

    repository = models.ForeignKey(
        "catalog.Repository",
        on_delete=models.CASCADE,
        related_name="pull_requests",
        verbose_name=_("repository"),
    )
    number = models.PositiveIntegerField(_("number"))
    github_id = models.CharField(_("GitHub node id"), max_length=100, unique=True)
    author = models.ForeignKey(
        "catalog.Identity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="authored_pull_requests",
        verbose_name=_("author"),
    )
    title = models.CharField(_("title"), max_length=500, blank=True)
    body = models.TextField(_("body"), blank=True)
    state = models.CharField(_("state"), max_length=10, choices=State.choices)
    is_draft = models.BooleanField(_("is draft"), default=False)
    base_ref = models.CharField(_("base ref"), max_length=200, blank=True)
    head_ref = models.CharField(_("head ref"), max_length=200, blank=True)

    created_at = models.DateTimeField(_("created at"))
    ready_for_review_at = models.DateTimeField(_("ready for review at"), null=True, blank=True)
    first_commit_at = models.DateTimeField(_("first commit at"), null=True, blank=True)
    first_review_at = models.DateTimeField(_("first review at"), null=True, blank=True)
    first_approval_at = models.DateTimeField(_("first approval at"), null=True, blank=True)
    merged_at = models.DateTimeField(_("merged at"), null=True, blank=True)
    closed_at = models.DateTimeField(_("closed at"), null=True, blank=True)
    last_activity_at = models.DateTimeField(_("last activity at"), null=True, blank=True)
    updated_at_github = models.DateTimeField(_("updated at (GitHub)"), null=True, blank=True)

    merged_by = models.ForeignKey(
        "catalog.Identity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="merged_pull_requests",
        verbose_name=_("merged by"),
    )
    merge_commit_sha = models.CharField(_("merge commit sha"), max_length=64, blank=True)
    merge_method = models.CharField(
        _("merge method"), max_length=10, choices=MergeMethod.choices, default=MergeMethod.UNKNOWN
    )

    additions = models.PositiveIntegerField(_("additions"), null=True, blank=True)
    deletions = models.PositiveIntegerField(_("deletions"), null=True, blank=True)
    changed_files = models.PositiveIntegerField(_("changed files"), null=True, blank=True)
    effective_additions = models.PositiveIntegerField(_("effective additions"), null=True, blank=True)
    effective_deletions = models.PositiveIntegerField(_("effective deletions"), null=True, blank=True)
    labels = models.JSONField(_("labels"), default=list, blank=True)
    review_rounds = models.PositiveIntegerField(_("review rounds"), null=True, blank=True)
    commits_after_first_review = models.PositiveIntegerField(
        _("commits after first review"), null=True, blank=True
    )

    raw = models.JSONField(_("raw payload"), null=True, blank=True)

    # Derived cache, recomputed by services.
    size_bucket = models.CharField(  # noqa: DJ001
        _("size bucket"), max_length=2, choices=SizeBucket.choices, null=True, blank=True
    )
    is_revert = models.BooleanField(_("is revert"), default=False)
    reverts_pr = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reverted_by",
        verbose_name=_("reverts PR"),
    )
    is_hotfix = models.BooleanField(_("is hotfix"), default=False)
    has_test_changes = models.BooleanField(_("has test changes"), default=False)
    ai_status = models.CharField(
        _("AI status"), max_length=20, choices=AIStatus.choices, default=AIStatus.UNKNOWN
    )
    ai_tools = models.JSONField(_("AI tools"), default=list, blank=True)
    ai_disclosure = models.CharField(
        _("AI disclosure"), max_length=20, choices=AIDisclosure.choices, default=AIDisclosure.MISSING
    )
    is_rubber_stamp = models.BooleanField(_("is rubber stamp"), default=False)
    is_self_merged = models.BooleanField(_("is self-merged"), default=False)

    class Meta:
        verbose_name = _("pull request")
        verbose_name_plural = _("pull requests")
        constraints = [
            models.UniqueConstraint(
                fields=["repository", "number"], name="uniq_pull_request_repository_number"
            ),
        ]
        indexes = [
            models.Index(fields=["repository", "merged_at"]),
            models.Index(fields=["author", "merged_at"]),
            models.Index(fields=["state", "is_draft", "last_activity_at"]),
            models.Index(fields=["ai_status", "merged_at"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.repository}#{self.number}"


class Commit(models.Model):
    repository = models.ForeignKey(
        "catalog.Repository", on_delete=models.CASCADE, related_name="commits", verbose_name=_("repository")
    )
    sha = models.CharField(_("sha"), max_length=64)
    github_id = models.CharField(_("GitHub node id"), max_length=100, unique=True, null=True, blank=True)
    author_identity = models.ForeignKey(
        "catalog.Identity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="authored_commits",
        verbose_name=_("author identity"),
    )
    author_email_identity = models.ForeignKey(
        "catalog.Identity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="authored_commits_by_email",
        verbose_name=_("author email identity"),
    )
    committer_identity = models.ForeignKey(
        "catalog.Identity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="committed_commits",
        verbose_name=_("committer identity"),
    )
    authored_at = models.DateTimeField(_("authored at"), null=True, blank=True)
    committed_at = models.DateTimeField(_("committed at"), null=True, blank=True)
    message = models.TextField(_("message"), blank=True)
    additions = models.PositiveIntegerField(_("additions"), null=True, blank=True)
    deletions = models.PositiveIntegerField(_("deletions"), null=True, blank=True)
    co_authors = models.JSONField(_("co-authors"), default=list, blank=True)
    trailers = models.JSONField(_("trailers"), default=dict, blank=True)
    raw = models.JSONField(_("raw payload"), null=True, blank=True)

    class Meta:
        verbose_name = _("commit")
        verbose_name_plural = _("commits")
        constraints = [
            models.UniqueConstraint(fields=["repository", "sha"], name="uniq_commit_repository_sha"),
        ]
        indexes = [models.Index(fields=["repository", "committed_at"])]

    def __str__(self) -> str:
        return self.sha[:12]


class PullRequestCommit(models.Model):
    pull_request = models.ForeignKey(
        PullRequest,
        on_delete=models.CASCADE,
        related_name="pull_request_commits",
        verbose_name=_("pull request"),
    )
    commit = models.ForeignKey(
        Commit, on_delete=models.CASCADE, related_name="pull_request_commits", verbose_name=_("commit")
    )
    position = models.PositiveIntegerField(_("position"))

    class Meta:
        verbose_name = _("pull request commit")
        verbose_name_plural = _("pull request commits")
        constraints = [
            models.UniqueConstraint(
                fields=["pull_request", "commit"], name="uniq_pr_commit_pull_request_commit"
            ),
        ]
        ordering = ["position"]

    def __str__(self) -> str:
        return f"{self.pull_request} @ {self.commit}"


class PRFile(models.Model):
    pull_request = models.ForeignKey(
        PullRequest, on_delete=models.CASCADE, related_name="files", verbose_name=_("pull request")
    )
    path = models.CharField(_("path"), max_length=1000)
    status = models.CharField(_("status"), max_length=20, blank=True)
    additions = models.PositiveIntegerField(_("additions"), null=True, blank=True)
    deletions = models.PositiveIntegerField(_("deletions"), null=True, blank=True)
    is_test = models.BooleanField(_("is test"), default=False)
    is_excluded = models.BooleanField(_("is excluded"), default=False)
    matched_sensitive_rule = models.ForeignKey(
        "policy.SensitivePathRule",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="matched_files",
        verbose_name=_("matched sensitive rule"),
    )

    class Meta:
        verbose_name = _("PR file")
        verbose_name_plural = _("PR files")
        constraints = [
            models.UniqueConstraint(fields=["pull_request", "path"], name="uniq_pr_file_pull_request_path"),
        ]
        indexes = [models.Index(fields=["pull_request", "is_excluded"])]

    def __str__(self) -> str:
        return self.path


class Review(models.Model):
    class State(models.TextChoices):
        APPROVED = "APPROVED", _("Approved")
        CHANGES_REQUESTED = "CHANGES_REQUESTED", _("Changes requested")
        COMMENTED = "COMMENTED", _("Commented")
        DISMISSED = "DISMISSED", _("Dismissed")

    pull_request = models.ForeignKey(
        PullRequest, on_delete=models.CASCADE, related_name="reviews", verbose_name=_("pull request")
    )
    reviewer = models.ForeignKey(
        "catalog.Identity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviews",
        verbose_name=_("reviewer"),
    )
    state = models.CharField(_("state"), max_length=20, choices=State.choices)
    submitted_at = models.DateTimeField(_("submitted at"), null=True, blank=True)
    body_length = models.PositiveIntegerField(_("body length"), null=True, blank=True)
    comments_count = models.PositiveIntegerField(_("comments count"), null=True, blank=True)
    github_id = models.CharField(_("GitHub node id"), max_length=100, unique=True)
    raw = models.JSONField(_("raw payload"), null=True, blank=True)

    class Meta:
        verbose_name = _("review")
        verbose_name_plural = _("reviews")
        indexes = [
            models.Index(fields=["pull_request", "submitted_at"]),
            models.Index(fields=["reviewer", "submitted_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.pull_request} review by {self.reviewer}"


class ReviewComment(models.Model):
    pull_request = models.ForeignKey(
        PullRequest, on_delete=models.CASCADE, related_name="review_comments", verbose_name=_("pull request")
    )
    author = models.ForeignKey(
        "catalog.Identity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="review_comments",
        verbose_name=_("author"),
    )
    created_at = models.DateTimeField(_("created at"), null=True, blank=True)
    is_review_thread = models.BooleanField(_("is review thread"), default=False)
    body_length = models.PositiveIntegerField(_("body length"), null=True, blank=True)
    github_id = models.CharField(_("GitHub node id"), max_length=100, unique=True)
    raw = models.JSONField(_("raw payload"), null=True, blank=True)

    class Meta:
        verbose_name = _("review comment")
        verbose_name_plural = _("review comments")

    def __str__(self) -> str:
        return f"comment on {self.pull_request}"


class CheckStatus(models.Model):
    class RollupState(models.TextChoices):
        SUCCESS = "SUCCESS", _("Success")
        FAILURE = "FAILURE", _("Failure")
        ERROR = "ERROR", _("Error")
        PENDING = "PENDING", _("Pending")
        NONE = "NONE", _("None")

    pull_request = models.ForeignKey(
        PullRequest, on_delete=models.CASCADE, related_name="check_statuses", verbose_name=_("pull request")
    )
    commit_sha = models.CharField(_("commit sha"), max_length=64)
    is_first_ci_commit = models.BooleanField(_("is first CI commit"), default=False)
    rollup_state = models.CharField(_("rollup state"), max_length=10, choices=RollupState.choices)
    observed_at = models.DateTimeField(_("observed at"), null=True, blank=True)

    class Meta:
        verbose_name = _("check status")
        verbose_name_plural = _("check statuses")
        constraints = [
            models.UniqueConstraint(
                fields=["pull_request", "commit_sha"], name="uniq_check_status_pr_commit_sha"
            ),
        ]
        indexes = [models.Index(fields=["pull_request", "is_first_ci_commit"])]

    def __str__(self) -> str:
        return f"{self.pull_request} @ {self.commit_sha[:12]}"
