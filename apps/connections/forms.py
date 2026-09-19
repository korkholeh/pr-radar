from django import forms
from django.utils.translation import gettext_lazy as _

from apps.connections.models import GitHubConnection


class ConnectionForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=200)
    kind = forms.ChoiceField(label=_("Kind"), choices=GitHubConnection.Kind.choices)
    owner_login = forms.CharField(
        label=_("Owner login"),
        max_length=200,
        required=False,
        help_text=_(
            "Optional label for the organization or account this connection belongs to. It does not "
            "limit discovery: every repository the token can read is listed, whoever owns it."
        ),
    )
    token = forms.CharField(
        label=_("Token"),
        widget=forms.PasswordInput(render_value=False),
        required=False,
        help_text=_("Leave blank to keep the current token."),
    )
    save_unverified = forms.BooleanField(
        label=_("Save without verifying"),
        required=False,
        help_text=_("Skip the GitHub check and store this token as unverified."),
    )

    def __init__(self, *args, require_token: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.require_token = require_token

    def clean(self):
        cleaned = super().clean()
        if self.require_token and not cleaned.get("token"):
            self.add_error("token", _("A token is required."))
        return cleaned


class RepositoryConnectionForm(forms.Form):
    """Moves one already-added repository to another connection. Only active connections are
    offered, and never the one the repository already uses — rebinding to itself is a no-op that
    would still write an audit entry."""

    connection = forms.ModelChoiceField(
        label=_("New connection"),
        queryset=GitHubConnection.objects.none(),
        empty_label=None,
        help_text=_("Only active connections are listed."),
    )
    confirm = forms.BooleanField(
        label=_("Yes, use this connection for future syncs"),
        help_text=_("Pull requests, reviews and commits already synced are kept."),
        error_messages={"required": _("Tick the confirmation box to change the connection.")},
    )

    def __init__(self, *args, repository=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = GitHubConnection.objects.filter(is_active=True).order_by("name")
        if repository is not None:
            queryset = queryset.exclude(pk=repository.connection_id)
        self.fields["connection"].queryset = queryset
