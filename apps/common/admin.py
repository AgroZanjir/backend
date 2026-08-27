from django.contrib import admin

from apps.common.models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("occurred_at", "level", "message_key", "subject", "user", "read_at")
    list_filter = ("level",)
    search_fields = ("subject", "message_key")
