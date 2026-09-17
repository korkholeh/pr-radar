from django.contrib import admin
from django.urls import include, path
from django.views.i18n import JavaScriptCatalog

js_catalog = JavaScriptCatalog.as_view(packages=["apps.accounts", "apps.dashboards"])

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("jsi18n/", js_catalog, name="javascript-catalog"),
    path("", include("apps.dashboards.urls")),
]
