from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.catalog.normalize import normalize_identity_value

SERIES_COLOR_CHOICES = [(f"series-{i}", f"series-{i}") for i in range(1, 9)]


class Organization(models.Model):
    class Type(models.TextChoices):
        ORG = "org", _("Organization")
        USER = "user", _("User")

    login = models.CharField(_("login"), max_length=200, unique=True)
    type = models.CharField(_("type"), max_length=10, choices=Type.choices)
    github_id = models.CharField(_("GitHub node id"), max_length=100, unique=True)
    is_active = models.BooleanField(_("is active"), default=True)
    raw = models.JSONField(_("raw payload"), null=True, blank=True)

    class Meta:
        verbose_name = _("organization")
        verbose_name_plural = _("organizations")

    def __str__(self) -> str:
        return self.login


class Repository(models.Model):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="repositories",
        verbose_name=_("organization"),
    )
    connection = models.ForeignKey(
        "connections.GitHubConnection",
        on_delete=models.PROTECT,
        related_name="repositories",
        verbose_name=_("connection"),
    )
    name = models.CharField(_("name"), max_length=200)
    full_name = models.CharField(_("full name"), max_length=400, unique=True)
    github_id = models.CharField(_("GitHub node id"), max_length=100, unique=True)
    default_branch = models.CharField(_("default branch"), max_length=200, blank=True)
    is_private = models.BooleanField(_("is private"), default=False)
    is_archived = models.BooleanField(_("is archived"), default=False)
    is_active = models.BooleanField(_("is active"), default=True)
    sync_since = models.DateField(_("sync since"), null=True, blank=True)
    last_synced_at = models.DateTimeField(_("last synced at"), null=True, blank=True)
    sync_cursor = models.JSONField(_("sync cursor"), default=dict, blank=True)
    merge_strategy_hint = models.CharField(_("merge strategy hint"), max_length=50, blank=True)
    raw = models.JSONField(_("raw payload"), null=True, blank=True)

    class Meta:
        verbose_name = _("repository")
        verbose_name_plural = _("repositories")
        indexes = [models.Index(fields=["is_active", "last_synced_at"])]

    def __str__(self) -> str:
        return self.full_name


class Project(models.Model):
    name = models.CharField(_("name"), max_length=200)
    slug = models.SlugField(_("slug"), max_length=200, unique=True)
    description = models.TextField(_("description"), blank=True)
    repositories = models.ManyToManyField(
        Repository,
        related_name="projects",
        blank=True,
        verbose_name=_("repositories"),
    )
    is_active = models.BooleanField(_("is active"), default=True)
    color = models.CharField(
        _("color"),
        max_length=20,
        choices=SERIES_COLOR_CHOICES,
        default="series-1",
    )

    class Meta:
        verbose_name = _("project")
        verbose_name_plural = _("projects")

    def __str__(self) -> str:
        return self.name


class Person(models.Model):
    class RoleHint(models.TextChoices):
        DEV = "dev", _("Developer")
        QA = "qa", _("QA")
        DESIGN = "design", _("Design")
        OTHER = "other", _("Other")

    display_name = models.CharField(_("display name"), max_length=200)
    is_active = models.BooleanField(_("is active"), default=True)
    is_bot = models.BooleanField(_("is bot"), default=False)
    exclude_from_metrics = models.BooleanField(_("exclude from metrics"), default=False)
    team = models.CharField(_("team"), max_length=200, blank=True)
    role_hint = models.CharField(_("role hint"), max_length=20, choices=RoleHint.choices, blank=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("person")
        verbose_name_plural = _("people")
        indexes = [models.Index(fields=["is_active", "is_bot"])]

    def __str__(self) -> str:
        return self.display_name


class Identity(models.Model):
    class Kind(models.TextChoices):
        GITHUB_LOGIN = "github_login", _("GitHub login")
        GIT_EMAIL = "git_email", _("Git email")

    person = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="identities",
        verbose_name=_("person"),
    )
    kind = models.CharField(_("kind"), max_length=20, choices=Kind.choices)
    value = models.CharField(_("value"), max_length=320)
    github_id = models.CharField(  # noqa: DJ001
        _("GitHub node id"), max_length=100, null=True, blank=True
    )
    first_seen_at = models.DateTimeField(_("first seen at"), auto_now_add=True)

    class Meta:
        verbose_name = _("identity")
        verbose_name_plural = _("identities")
        constraints = [
            models.UniqueConstraint(fields=["kind", "value"], name="uniq_identity_kind_value"),
        ]
        indexes = [models.Index(fields=["person"])]

    def __str__(self) -> str:
        return f"{self.kind}:{self.value}"

    def save(self, *args: object, **kwargs: object) -> None:
        # Bulk writers (bulk_create, bulk_update, QuerySet.update) bypass save() and clean()
        # alike, so any batch identity writer must call normalize_identity_value() itself.
        self.value = normalize_identity_value(self.kind, self.value)
        super().save(*args, **kwargs)

    def clean(self) -> None:
        self.value = normalize_identity_value(self.kind, self.value)


class AppSetting(models.Model):
    key = models.CharField(_("key"), max_length=100, unique=True)
    value_type = models.CharField(_("value type"), max_length=10)
    value = models.JSONField(_("value"))
    description = models.CharField(_("description"), max_length=400, blank=True)

    class Meta:
        verbose_name = _("app setting")
        verbose_name_plural = _("app settings")
        permissions = [("manage_settings", _("Can manage settings"))]

    def __str__(self) -> str:
        return self.key

    def clean(self) -> None:
        from django.core.exceptions import ValidationError

        from apps.catalog.services import _validate_type

        try:
            _validate_type(self.value_type, self.value)
        except ValidationError as exc:
            raise ValidationError({"value": exc.message}) from exc
