from django.urls import path

from apps.dashboards import views

app_name = "dashboards"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("prs/<int:pk>/", views.pull_request_detail, name="pull_request_detail"),
]
