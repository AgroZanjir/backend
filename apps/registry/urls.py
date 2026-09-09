from django.urls import path

from apps.registry import auth, views

app_name = "registry"

urlpatterns = [
    # sessions
    path("auth/oneid/", auth.oneid_sign_in, name="oneid"),
    path("auth/password/", auth.password_sign_in, name="password"),
    path("auth/refresh/", auth.refresh, name="refresh"),
    path("auth/logout/", auth.sign_out, name="logout"),
    path("auth/me/", auth.me, name="me"),
    path("auth/personas/", auth.personas, name="personas"),
    # the registry itself
    path("organisations/mine/", views.my_organisations, name="my-orgs"),
    path("organisations/<str:code>/checks/", views.decide_check, name="decide-check"),
    path("farms/", views.create_farm, name="farm-create"),
    path("users/invite/", views.invite, name="invite"),
    path("users/<int:user_id>/status/", views.set_user_status, name="user-status"),
    path("users/<int:user_id>/role/", views.set_user_role, name="user-role"),
]
