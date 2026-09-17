from django.urls import path

from apps.ai_detection import views

app_name = "ai_detection"

urlpatterns = [
    path("settings/detection-rules/", views.rules_list, name="rules"),
    path("settings/detection-rules/new/", views.rule_create, name="rule_create"),
    path("settings/detection-rules/<int:pk>/edit/", views.rule_edit, name="rule_edit"),
    path("settings/detection-rules/<int:pk>/toggle/", views.rule_toggle, name="rule_toggle"),
    path("settings/detection-rules/dry-run/", views.rule_dry_run, name="rule_dry_run"),
]
