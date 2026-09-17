import itertools

import pytest
from django.urls import URLPattern, URLResolver, get_resolver, reverse

ALLOWED_ANONYMOUS_NAMES = {
    "accounts:login",
    "accounts:logout",
    "accounts:password_reset",
    "accounts:password_reset_done",
    "accounts:password_reset_confirm",
    "accounts:password_reset_complete",
    "accounts:set_theme",
    "accounts:set_language",
}


SAMPLE_ARGS = {
    "uidb64": "MQ",
    "token": "set-password",
    "pk": 1,
}


def _named_urls(resolver=None, namespace_prefix=""):
    """Recursively walk urlpatterns so a nested include() can't escape the deny-by-default test.

    The admin namespace is walked separately (test_admin_root_redirects_anonymous_to_admin_login),
    since it has its own login and hundreds of generated model URLs that add nothing here.
    """
    resolver = resolver or get_resolver()
    names = []
    for pattern in resolver.url_patterns:
        if isinstance(pattern, URLPattern):
            if pattern.name:
                names.append(f"{namespace_prefix}{pattern.name}")
        elif isinstance(pattern, URLResolver):
            if pattern.namespace == "admin":
                continue
            prefix = f"{namespace_prefix}{pattern.namespace}:" if pattern.namespace else namespace_prefix
            names.extend(_named_urls(pattern, prefix))
    return sorted(set(names))


def _reverse(name):
    """Tries every combination of SAMPLE_ARGS keys (including none) — a URL pattern only
    accepts the exact kwargs it declares, so a fixed guess (e.g. "the full dict" or "one key
    at a time") misses patterns needing more than one key but not all of them
    (password_reset_confirm takes uidb64+token, not pk)."""
    keys = list(SAMPLE_ARGS)
    for size in range(len(keys) + 1):
        for combo in itertools.combinations(keys, size):
            kwargs = {key: SAMPLE_ARGS[key] for key in combo}
            try:
                return reverse(name, kwargs=kwargs) if kwargs else reverse(name)
            except Exception:
                continue
    return None


UNREVERSIBLE_URL_NAMES = [name for name in _named_urls() if _reverse(name) is None]
ALL_NAMED_URLS = [name for name in _named_urls() if _reverse(name) is not None]


def test_every_named_url_is_reversible_with_the_sample_args():
    """A URL that SAMPLE_ARGS can't reverse silently disappears from the deny-by-default
    parametrization below instead of failing loudly, so a future URL with e.g. a `pk` argument
    would stop being checked with no warning. Add its sample value to SAMPLE_ARGS instead."""
    assert not UNREVERSIBLE_URL_NAMES, (
        f"cannot reverse {UNREVERSIBLE_URL_NAMES} with SAMPLE_ARGS={SAMPLE_ARGS} — "
        "add sample args for these names to SAMPLE_ARGS"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("name", ALL_NAMED_URLS)
def test_anonymous_is_redirected_unless_allowlisted(client, name):
    url = _reverse(name)
    response = client.get(url)
    if name in ALLOWED_ANONYMOUS_NAMES:
        assert response.status_code in (200, 405), f"{name} -> {response.status_code}"
    else:
        assert response.status_code == 302, f"{name} -> {response.status_code}"
        assert response.url.startswith(reverse("accounts:login"))


def test_admin_root_redirects_anonymous_to_admin_login(client):
    response = client.get("/admin/")
    assert response.status_code == 302
    assert response.url.startswith("/admin/login/")


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["dashboards:overview", "javascript-catalog"])
def test_authenticated_lead_gets_200(client, lead_user, name):
    client.force_login(lead_user)
    url = reverse(name)
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db
def test_login_with_wrong_password_renders_visible_field_error(client, lead_user):
    response = client.post(
        reverse("accounts:login"), {"username": lead_user.username, "password": "wrong-password"}
    )
    assert response.status_code == 200
    content = response.content.decode()
    assert 'role="alert"' in content


@pytest.mark.django_db
def test_login_honours_next(client, lead_user):
    target = reverse("dashboards:overview")
    response = client.post(
        f"{reverse('accounts:login')}?next={target}",
        {"username": lead_user.username, "password": "lead-pass"},
    )
    assert response.status_code == 302
    assert response.url == target
