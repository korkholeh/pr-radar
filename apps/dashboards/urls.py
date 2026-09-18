from django.urls import path

from apps.dashboards import views
from apps.metrics.models import ScopeType

app_name = "dashboards"

urlpatterns = [
    path("", views.dashboard, {"scope_type": ScopeType.GLOBAL}, name="overview"),
    path("projects/", views.projects_index, name="projects_index"),
    path("projects/<int:pk>/", views.dashboard, {"scope_type": ScopeType.PROJECT}, name="project"),
    path("repos/", views.repositories_index, name="repositories_index"),
    path("repos/<int:pk>/", views.dashboard, {"scope_type": ScopeType.REPO}, name="repository"),
    path("api/charts/<slug:chart_key>/", views.chart_json, name="chart_json"),
    path("export/<slug:table_key>.<slug:fmt>", views.export_table, name="export"),
    path("prs/<int:pk>/", views.pull_request_detail, name="pull_request_detail"),
]
