from django.contrib import admin

from apps.commercial.models import (
    ExportContract,
    OfftakeContract,
    PriceTerm,
    Shipment,
    ShipmentLine,
)


class PriceTermInline(admin.TabularInline):
    model = PriceTerm
    fk_name = "export_contract"
    extra = 0


@admin.register(ExportContract)
class ExportContractAdmin(admin.ModelAdmin):
    list_display = ("code", "buyer_name", "buyer_country", "product", "quantity_g", "amount_minor", "currency", "status")
    list_filter = ("status", "buyer_country", "payment_terms")
    search_fields = ("code", "buyer_name")
    autocomplete_fields = ("seller_party", "product")
    inlines = (PriceTermInline,)


@admin.register(OfftakeContract)
class OfftakeContractAdmin(admin.ModelAdmin):
    list_display = ("code", "seller_party", "buyer_party", "product", "quantity_g", "status")
    list_filter = ("status",)
    search_fields = ("code",)
    autocomplete_fields = ("seller_party", "buyer_party", "product")


class ShipmentLineInline(admin.TabularInline):
    model = ShipmentLine
    extra = 0
    autocomplete_fields = ("lot",)


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ("code", "export_contract", "carrier_party", "mode", "status", "departs_at", "eta")
    list_filter = ("status", "mode")
    search_fields = ("code", "vehicle")
    autocomplete_fields = ("export_contract", "carrier_party")
    inlines = (ShipmentLineInline,)
