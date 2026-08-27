from django.contrib import admin

from apps.governance.models import AuditEntry, DataGrant


@admin.register(AuditEntry)
class AuditEntryAdmin(admin.ModelAdmin):
    """Read-only on purpose: an audit log an administrator can edit is not one."""

    list_display = ("occurred_at", "actor_label", "action_key", "object_ref", "capability")
    list_filter = ("action_key", "capability")
    search_fields = ("actor_label", "object_ref")
    date_hierarchy = "occurred_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DataGrant)
class DataGrantAdmin(admin.ModelAdmin):
    list_display = ("grantee_label", "scope_key", "fields_key", "basis", "status", "expires_on")
    list_filter = ("basis", "status")
