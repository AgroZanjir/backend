from django.urls import path

from apps.lots import views

app_name = "lots"

urlpatterns = [
    path("", views.register, name="register"),
    path("<str:code>/grade/", views.grade, name="grade"),
    path("<str:code>/split/", views.split, name="split"),
    path("<str:code>/reserve/", views.reserve, name="reserve"),
    path("<str:code>/dispatch/", views.dispatch, name="dispatch"),
    path("<str:code>/write-off/", views.write_off, name="write-off"),
    path("<str:code>/verify-chain/", views.verify_chain, name="verify-chain"),
]
