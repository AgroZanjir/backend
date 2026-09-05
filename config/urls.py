from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from apps.common.views import health, index

api_v1 = [
    path("health/", health, name="health"),
    # Each cluster owns its own writes (section 06). `panels` owns the reads
    # that span clusters, and only those - see apps/panels/models.py for why
    # that composition has a module of its own instead of living in one of the
    # clusters it joins.
    path("", include("apps.registry.urls")),
    path("lots/", include("apps.lots.urls")),
    path("quality/", include("apps.quality.urls")),
    path("storage/", include("apps.storage.urls")),
    path("commercial/", include("apps.commercial.urls")),
    path("finance/", include("apps.finance.urls")),
    path("documents/", include("apps.documents.urls")),
    path("governance/", include("apps.governance.urls")),
    path("panels/", include("apps.panels.urls")),
    # Not a cluster and not one of the five ports: the public website's
    # assistant, which reads the two open endpoints above and nothing else.
    path("assistant/", include("apps.assistant.urls")),
    # Nor is the contact form: the website's one write, open to anybody.
    path("", include("apps.website.urls")),
]

urlpatterns = [
    # The website is a separate origin; this is only a signpost for whoever
    # opens the API host itself.
    path("", index, name="index"),
    # The admin is the manual adapter surface from figure 4 - it is a
    # deliverable here, not a developer convenience.
    path("admin/", admin.site.urls),
    path("api/v1/", include(api_v1)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
