from django.urls import path

from apps.governance import views

app_name = "governance"

urlpatterns = [
    path("grants/", views.create_grant, name="grant-create"),
    path("grants/<uuid:grant_id>/revoke/", views.revoke_grant, name="grant-revoke"),
]
