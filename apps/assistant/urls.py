from django.urls import path

from apps.assistant import views

app_name = "assistant"

urlpatterns = [
    path("", views.state, name="state"),
    path("ask/", views.ask, name="ask"),
    path("panel/ask/", views.panel_ask, name="panel-ask"),
]
