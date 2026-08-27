"""The registry admin.

This is the manual-adapter surface from figure 4, not a developer convenience:
verification decisions, memberships and the role catalogue are keyed in here
until each has a screen of its own.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from apps.registry.models import (
    Capability,
    Farm,
    Membership,
    OrganisationType,
    Party,
    PartyVerification,
    Product,
    ProductionPlan,
    Role,
    User,
    VerificationCheck,
)


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    autocomplete_fields = ("party", "role")


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("display_name", "username", "status", "oneid_verified", "eimzo_verified", "last_seen_at")
    list_filter = ("status", "oneid_verified", "eimzo_verified", "is_staff")
    search_fields = ("display_name", "username", "email", "pinfl")
    inlines = (MembershipInline,)
    fieldsets = DjangoUserAdmin.fieldsets + (
        (
            "Agro Zanjir",
            {
                "fields": (
                    "display_name",
                    "pinfl",
                    "phone",
                    "status",
                    "oneid_verified",
                    "eimzo_verified",
                    "last_seen_at",
                )
            },
        ),
    )


class PartyVerificationInline(admin.TabularInline):
    model = PartyVerification
    extra = 0
    autocomplete_fields = ("verification_check",)


@admin.register(Party)
class PartyAdmin(admin.ModelAdmin):
    list_display = ("code", "legal_name", "type", "region", "verification_status", "verified_on")
    list_filter = ("verification_status", "type", "region")
    search_fields = ("code", "legal_name", "tin")
    inlines = (PartyVerificationInline, MembershipInline)
    autocomplete_fields = ("type",)


@admin.register(OrganisationType)
class OrganisationTypeAdmin(admin.ModelAdmin):
    list_display = ("code", "label_key", "licence_register", "sort_order")
    search_fields = ("code",)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("code", "group_key", "scope", "capability_list")
    list_filter = ("group_key", "scope")
    search_fields = ("code",)
    filter_horizontal = ("capabilities",)

    @admin.display(description="capabilities")
    def capability_list(self, obj):
        return ", ".join(c.code for c in obj.capabilities.all())


@admin.register(Capability)
class CapabilityAdmin(admin.ModelAdmin):
    list_display = ("code", "label_key", "sort_order")
    search_fields = ("code",)


@admin.register(VerificationCheck)
class VerificationCheckAdmin(admin.ModelAdmin):
    list_display = ("code", "register", "mode", "sort_order")
    search_fields = ("code",)


@admin.register(PartyVerification)
class PartyVerificationAdmin(admin.ModelAdmin):
    list_display = ("party", "verification_check", "result", "decided_at", "decided_by")
    list_filter = ("result", "verification_check")
    autocomplete_fields = ("party", "verification_check", "decided_by")


@admin.register(Farm)
class FarmAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "party", "region", "district", "hectares")
    list_filter = ("region",)
    search_fields = ("code", "name", "owner_name")
    autocomplete_fields = ("party",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("code", "name_uz", "name_ru", "name_en", "variety", "hs_code")
    search_fields = ("code", "name_uz", "name_ru", "name_en")


@admin.register(ProductionPlan)
class ProductionPlanAdmin(admin.ModelAdmin):
    list_display = ("farm", "product", "season_year", "planted_hectares", "harvest_from", "harvest_to")
    list_filter = ("season_year", "product")
    autocomplete_fields = ("farm", "product")
