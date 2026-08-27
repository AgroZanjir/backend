from django.contrib import admin

from apps.storage.models import (
    ConditionExcursion,
    ConditionReading,
    Facility,
    GateArrival,
    StoragePlacement,
    StorageZone,
)


class StorageZoneInline(admin.TabularInline):
    model = StorageZone
    extra = 0
    fields = ("code", "mode", "capacity_g", "target_temp_c", "target_rh_pct")


@admin.register(Facility)
class FacilityAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "kind", "operator_party", "region")
    list_filter = ("kind", "region")
    search_fields = ("code", "name")
    autocomplete_fields = ("operator_party",)
    inlines = (StorageZoneInline,)


@admin.register(StorageZone)
class StorageZoneAdmin(admin.ModelAdmin):
    list_display = ("code", "facility", "mode", "capacity_g", "fill", "current_temp_c", "target_temp_c", "off_band")
    list_filter = ("mode", "facility")
    search_fields = ("code",)

    @admin.display(description="used (g)")
    def fill(self, obj):
        return obj.used_g


@admin.register(GateArrival)
class GateArrivalAdmin(admin.ModelAdmin):
    list_display = ("expected_at", "vehicle", "farm", "product", "estimated_weight_g", "status", "lot_code")
    list_filter = ("status", "facility")
    search_fields = ("vehicle", "lot_code")
    autocomplete_fields = ("facility", "farm", "product")


@admin.register(StoragePlacement)
class StoragePlacementAdmin(admin.ModelAdmin):
    list_display = ("lot", "zone", "position", "quantity_g", "placed_at", "removed_at")
    list_filter = ("zone",)
    search_fields = ("lot__code", "position")
    autocomplete_fields = ("lot", "zone")


@admin.register(ConditionReading)
class ConditionReadingAdmin(admin.ModelAdmin):
    """Read-only: readings arrive through the sensor port, not by hand."""

    list_display = ("recorded_at", "scope_type", "scope_code", "sensor_id", "temp_c", "rh_pct")
    list_filter = ("scope_type", "sensor_id")
    search_fields = ("scope_code", "sensor_id")
    date_hierarchy = "recorded_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ConditionExcursion)
class ConditionExcursionAdmin(admin.ModelAdmin):
    list_display = ("code", "scope_code", "metric", "severity", "started_at", "ended_at", "peak_value", "resolved")
    list_filter = ("severity", "metric", "resolved")
    search_fields = ("code", "scope_code")
