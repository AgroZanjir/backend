from django.urls import path

from apps.storage import views

app_name = "storage"

urlpatterns = [
    path("placements/", views.place, name="place"),
    path("placements/remove/", views.remove, name="remove"),
    path("arrivals/<uuid:arrival_id>/weigh/", views.weigh, name="weigh"),
    path("readings/", views.ingest_readings, name="readings"),
    path("excursions/<str:code>/resolve/", views.resolve_excursion, name="excursion-resolve"),
]
