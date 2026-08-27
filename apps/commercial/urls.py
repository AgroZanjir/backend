from django.urls import path

from apps.commercial import views

app_name = "commercial"

urlpatterns = [
    path("shipments/", views.create_shipment, name="shipment-create"),
    path("shipments/<str:code>/depart/", views.depart, name="shipment-depart"),
    path("shipments/<str:code>/deliver/", views.deliver, name="shipment-deliver"),
    path("exports/<str:code>/declaration/", views.lodge_declaration, name="declaration"),
]
