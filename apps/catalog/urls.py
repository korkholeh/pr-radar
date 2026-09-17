from django.urls import path

from apps.catalog import views

app_name = "catalog"

urlpatterns = [
    path("settings/people/", views.people_list, name="people"),
    path("settings/people/new/", views.person_create, name="person_create"),
    path("settings/people/<int:pk>/edit/", views.person_edit, name="person_edit"),
    path("settings/people/merge/", views.person_merge, name="person_merge"),
    path("settings/people/identities/", views.identity_queue, name="identity_queue"),
    path("settings/people/identities/<int:pk>/assign/", views.identity_assign, name="identity_assign"),
    path(
        "settings/people/identities/<int:pk>/create-person/",
        views.identity_create_person,
        name="identity_create_person",
    ),
    path("settings/people/identities/<int:pk>/mark-bot/", views.identity_mark_bot, name="identity_mark_bot"),
    path("settings/people/identities/<int:pk>/exclude/", views.identity_exclude, name="identity_exclude"),
]
