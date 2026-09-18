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
    path("people/", views.people_index, name="people_index"),
    path("people/<int:pk>/", views.dashboard, {"scope_type": ScopeType.PERSON}, name="person"),
    path("people/<int:pk>/notes/", views.person_notes, name="person_notes"),
    path("api/charts/<slug:chart_key>/", views.chart_json, name="chart_json"),
    path("export/<slug:table_key>.<slug:fmt>", views.export_table, name="export"),
    path("report.xlsx", views.export_report, name="export_report"),
    path("exports/", views.exports_index, name="exports_index"),
    path("exports/<int:pk>/download/", views.export_download, name="export_download"),
    path("reviews/", views.reviews_page, name="reviews"),
    path("prs/", views.pull_requests_index, name="pull_requests_index"),
    path("prs/<int:pk>/", views.pull_request_detail, name="pull_request_detail"),
    path(
        "prs/<int:pk>/violations/",
        views.pull_request_violation_action,
        name="pull_request_violation_action",
    ),
]
