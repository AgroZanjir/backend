from django.urls import path

from apps.website import views

app_name = "website"

urlpatterns = [
    path("enquiries/", views.enquire, name="enquire"),
]
