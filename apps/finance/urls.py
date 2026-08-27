from django.urls import path

from apps.finance import views

app_name = "finance"

urlpatterns = [
    path("applications/", views.create_application, name="application-create"),
    path("applications/<str:code>/submit/", views.submit_application, name="application-submit"),
    path("applications/<str:code>/decide/", views.decide_application, name="application-decide"),
    path("liens/", views.create_lien, name="lien-create"),
    path("liens/<uuid:lien_id>/release/", views.release_lien, name="lien-release"),
    path("claims/", views.create_claim, name="claim-create"),
    path("claims/<str:code>/decide/", views.decide_claim, name="claim-decide"),
]
