"""The spine's admin.

`LotEvent` is registered read-only: the log is append-only in the model, and an
admin form that appears to let someone edit it would be a lie about what the
system guarantees.
"""

from django.contrib import admin

from apps.lots.models import Lot, LotEvent, LotRelation, dispatch_blockers


class LotEventInline(admin.TabularInline):
    model = LotEvent
    extra = 0
    can_delete = False
    readonly_fields = ("sequence", "event_type", "occurred_at", "actor_label", "payload", "hash")
    fields = readonly_fields
    ordering = ("sequence",)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Lot)
class LotAdmin(admin.ModelAdmin):
    list_display = ("code", "product", "status", "grade", "net_weight_g", "storage_mode", "sell_by", "blockers")
    list_filter = ("status", "storage_mode", "grade", "product")
    search_fields = ("code",)
    autocomplete_fields = ("product", "origin_farm", "owner_party")
    inlines = (LotEventInline,)
    readonly_fields = ("chain_intact",)

    @admin.display(description="dispatch blockers")
    def blockers(self, obj):
        return "; ".join(dispatch_blockers(obj)) or "—"


@admin.register(LotEvent)
class LotEventAdmin(admin.ModelAdmin):
    list_display = ("lot", "sequence", "event_type", "occurred_at", "actor_label", "severity")
    list_filter = ("event_type", "severity")
    search_fields = ("lot__code", "actor_label")
    date_hierarchy = "occurred_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LotRelation)
class LotRelationAdmin(admin.ModelAdmin):
    list_display = ("parent", "kind", "child", "quantity_g")
    list_filter = ("kind",)
    autocomplete_fields = ("parent", "child")
