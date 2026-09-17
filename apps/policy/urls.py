from django.urls import path

from apps.policy import views

app_name = "policy"

urlpatterns = [
    path("policy/", views.console, name="console"),
    path("policy/violations/action/", views.violation_bulk_action, name="violation_bulk_action"),
    path("settings/ai-policy/", views.policy_settings, name="policy_settings"),
    path("settings/sensitive-paths/", views.sensitive_paths, name="sensitive_paths"),
    path("settings/sensitive-paths/new/", views.sensitive_path_create, name="sensitive_path_create"),
    path("settings/sensitive-paths/<int:pk>/edit/", views.sensitive_path_edit, name="sensitive_path_edit"),
    path(
        "settings/sensitive-paths/<int:pk>/toggle/", views.sensitive_path_toggle, name="sensitive_path_toggle"
    ),
]
