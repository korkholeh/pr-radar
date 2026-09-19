"""Dev-only wall-clock/SQL profiling for the dashboard pages, against `seed_demo --scale large`
data (plan T10, ARCHITECTURE line 333: 90-day Overview renders < 1.5s cold / < 0.3s warm on 50
repos / 20,000 PRs). Not imported by the app; run manually:

    uv run python manage.py seed_demo --reset --scale large
    uv run python scripts/profile_dashboard.py

Renders the Overview / a project / a repository / the people index / Reviews through the Django
test client with `CaptureQueriesContext`, printing per-page wall time and the ten slowest SQL
statements, so `tests/test_performance.py`'s pinned budget and any `select_related`/index fix
(T11) have a reproducible, committed measurement behind them rather than a one-off number in a
chat transcript."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402

settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "testserver"]

from django.contrib.auth import get_user_model  # noqa: E402
from django.contrib.auth.models import Group  # noqa: E402
from django.db import connection, reset_queries  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import CaptureQueriesContext  # noqa: E402
from django.urls import reverse  # noqa: E402

from apps.catalog.models import Project, Repository  # noqa: E402

User = get_user_model()

PERIOD_QS = "preset=90d"


def _profiling_client() -> Client:
    user, _created = User.objects.get_or_create(
        username="profile-dashboard-script", defaults={"is_active": True}
    )
    group, _created = Group.objects.get_or_create(name="lead")
    user.groups.add(group)
    client = Client()
    client.force_login(user)
    return client


def _page_urls() -> list[tuple[str, str]]:
    project = Project.objects.filter(slug__startswith="demo-project-large-").first()
    repository = Repository.objects.filter(full_name__startswith="pr-radar-large-").first()
    if project is None or repository is None:
        raise SystemExit(
            "No large-scale data found. Run `uv run python manage.py seed_demo --reset --scale large` first."
        )
    return [
        ("Overview", reverse("dashboards:overview") + f"?{PERIOD_QS}"),
        ("Project", reverse("dashboards:project", args=[project.pk]) + f"?{PERIOD_QS}"),
        ("Repository", reverse("dashboards:repository", args=[repository.pk]) + f"?{PERIOD_QS}"),
        ("People", reverse("dashboards:people_index") + f"?{PERIOD_QS}"),
        ("Reviews", reverse("dashboards:reviews") + f"?{PERIOD_QS}"),
    ]


def _profile_page(client: Client, name: str, url: str) -> None:
    reset_queries()
    with CaptureQueriesContext(connection) as ctx:
        started = time.perf_counter()
        response = client.get(url)
        elapsed = time.perf_counter() - started
    status = response.status_code
    queries = sorted(ctx.captured_queries, key=lambda q: float(q["time"]), reverse=True)
    print(f"\n== {name} ({url}) ==")
    print(f"status={status} wall_time={elapsed:.3f}s queries={len(ctx.captured_queries)}")
    print("slowest statements:")
    for query in queries[:10]:
        sql = query["sql"]
        sql = sql if len(sql) <= 200 else sql[:200] + "…"
        print(f"  {float(query['time']):.4f}s  {sql}")


def main() -> None:
    client = _profiling_client()
    for name, url in _page_urls():
        _profile_page(client, name, url)


if __name__ == "__main__":
    main()
