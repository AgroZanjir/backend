"""One URL per thing a panel screen reads.

Named for what the screen asks for, not for the tables behind it: the bank's
collateral screen wants a lot passport, not a join across four clusters.
"""

from django.urls import path

from apps.panels import views

app_name = "panels"

urlpatterns = [
    path("reference/", views.reference, name="reference"),
    path("lots/", views.lots, name="lots"),
    path("lots/<str:code>/", views.lot_passport, name="lot"),
    path("public/lots/<str:code>/", views.public_passport, name="public-lot"),
    path("public/trials/<str:code>/", views.public_trial, name="public-trial"),
    path("zones/", views.zones, name="zones"),
    path("arrivals/", views.arrivals, name="arrivals"),
    path("readings/", views.readings, name="readings"),
    path("excursions/", views.excursions, name="excursions"),
    path("excursions/<str:code>/", views.excursion, name="excursion"),
    path("trials/", views.trials, name="trials"),
    path("trials/<str:code>/", views.trial, name="trial"),
    path("qc-records/", views.qc_records, name="qc"),
    path("finance/applications/", views.applications, name="applications"),
    path("finance/liens/", views.liens, name="liens"),
    path("policies/", views.policies, name="policies"),
    path("claims/", views.claims, name="claims"),
    path("exports/", views.exports, name="exports"),
    path("shipments/", views.shipments, name="shipments"),
    path("shipments/<str:code>/", views.shipment, name="shipment"),
    path("documents/", views.documents, name="documents"),
    path("notifications/", views.notifications, name="notifications"),
    path("admin/organisations/", views.organisations, name="organisations"),
    path("admin/organisations/<str:code>/", views.organisation, name="organisation"),
    path("admin/users/", views.platform_users, name="users"),
    path("admin/audit/", views.audit_log, name="audit"),
    path("admin/grants/", views.grants, name="grants"),
]
