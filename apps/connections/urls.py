from django.urls import path

from apps.connections import views

app_name = "connections"

urlpatterns = [
    path("settings/connections/", views.connection_list, name="list"),
    path("settings/connections/new/", views.connection_create, name="create"),
    path("settings/connections/<int:pk>/edit/", views.connection_edit, name="edit"),
    path("settings/connections/<int:pk>/check/", views.connection_check, name="check"),
    path("settings/connections/<int:pk>/deactivate/", views.connection_deactivate, name="deactivate"),
    path("settings/connections/<int:pk>/delete/", views.connection_delete, name="delete"),
    path("settings/repositories/discover/", views.repository_discover, name="discover"),
    path("settings/repositories/<int:pk>/rebind/", views.repository_rebind, name="rebind"),
]
