from django.urls import path

from apps.quality import views

app_name = "quality"

urlpatterns = [
    path("qc-records/", views.create_qc_record, name="qc-create"),
    path("trials/", views.create_trial, name="trial-create"),
    path("trials/<str:code>/observations/", views.create_observation, name="observe"),
]
