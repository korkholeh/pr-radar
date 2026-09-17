from django.urls import path

from apps.github_sync import views

app_name = "github_sync"

urlpatterns = [
    path("sync/", views.sync_page, name="sync"),
    path("sync/run/", views.sync_run, name="run"),
    path("sync/status/", views.sync_status, name="status"),
]
