from django.urls import path

from apps.ai_detection import views

app_name = "ai_detection"

urlpatterns = [
    path("settings/detection-rules/", views.rules_list, name="rules"),
    path("settings/detection-rules/new/", views.rule_create, name="rule_create"),
    path("settings/detection-rules/<int:pk>/edit/", views.rule_edit, name="rule_edit"),
    path("settings/detection-rules/<int:pk>/toggle/", views.rule_toggle, name="rule_toggle"),
    path("settings/detection-rules/dry-run/", views.rule_dry_run, name="rule_dry_run"),
    path("settings/structural-signals/", views.signal_rules_list, name="signal_rules"),
    path("settings/structural-signals/new/", views.signal_rule_create, name="signal_rule_create"),
    path("settings/structural-signals/<int:pk>/edit/", views.signal_rule_edit, name="signal_rule_edit"),
    path(
        "settings/structural-signals/<int:pk>/toggle/",
        views.signal_rule_toggle,
        name="signal_rule_toggle",
    ),
    path("settings/structural-signals/dry-run/", views.signal_rule_dry_run, name="signal_rule_dry_run"),
]
