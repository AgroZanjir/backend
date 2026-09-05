"""Where enquiries are read and answered until they have a screen."""

from django.contrib import admin

from apps.website.models import Enquiry


@admin.register(Enquiry)
class EnquiryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "name", "organisation", "topic", "status")
    list_filter = ("status", "topic", "created_at")
    search_fields = ("name", "organisation", "email", "phone", "message")
    # Everything a visitor typed is a record of what they said, not a draft to
    # be edited: only the handling status is ours to change.
    readonly_fields = (
        "created_at",
        "name",
        "organisation",
        "email",
        "phone",
        "topic",
        "message",
        "source_address",
    )
    list_per_page = 50
