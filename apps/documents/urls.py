from django.urls import path

from apps.documents import views

app_name = "documents"

urlpatterns = [
    path("", views.create_document, name="create"),
]
