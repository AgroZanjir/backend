"""The finance admin - where the manual lender and insurer adapters land.

An operator keys the bank's decision in here after a phone call, and the rest
of the system cannot tell the difference between that and an API adapter.
"""

from django.contrib import admin

from apps.finance.models import (
    Claim,
    Encumbrance,
    FinanceApplication,
    Policy,
    SettlementAllocation,
)


@admin.register(FinanceApplication)
class FinanceApplicationAdmin(admin.ModelAdmin):
    list_display = ("code", "applicant_party", "lender_party", "kind", "amount_minor", "currency", "status", "applied_on")
    list_filter = ("status", "kind", "currency")
    search_fields = ("code", "port_reference")
    autocomplete_fields = ("applicant_party", "lender_party")
    filter_horizontal = ("collateral_lots",)


@admin.register(Encumbrance)
class EncumbranceAdmin(admin.ModelAdmin):
    """Releases happen through the model, so the lot's log records them."""

    list_display = ("lot", "application", "holder_party", "amount_minor", "currency", "created_on", "released_at")
    list_filter = ("currency",)
    search_fields = ("lot__code", "application__code")
    autocomplete_fields = ("lot", "application", "holder_party")
    actions = ("release_selected",)

    @admin.action(description="Release the selected liens")
    def release_selected(self, request, queryset):
        released = 0
        for lien in queryset.filter(released_at__isnull=True):
            lien.release(reference=f"admin:{request.user}")
            released += 1
        self.message_user(request, f"{released} lien(s) released and written to the lot log.")


@admin.register(Policy)
class PolicyAdmin(admin.ModelAdmin):
    list_display = ("code", "insurer_party", "holder_party", "kind", "status", "starts_on", "ends_on")
    list_filter = ("kind", "status")
    search_fields = ("code",)
    autocomplete_fields = ("insurer_party", "holder_party")
    filter_horizontal = ("covered_lots",)


@admin.register(Claim)
class ClaimAdmin(admin.ModelAdmin):
    list_display = ("code", "policy", "lot", "status", "amount_minor", "assessed_minor", "filed_on", "decided_on")
    list_filter = ("status",)
    search_fields = ("code", "excursion_code")
    autocomplete_fields = ("policy", "lot")


@admin.register(SettlementAllocation)
class SettlementAllocationAdmin(admin.ModelAdmin):
    list_display = ("export_contract_code", "priority", "category", "party", "amount_minor", "currency", "status")
    list_filter = ("category", "status")
    search_fields = ("export_contract_code",)
    autocomplete_fields = ("party",)
