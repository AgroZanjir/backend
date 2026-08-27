from django.contrib import admin

from apps.documents.models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("code", "subject_type", "subject_code", "doc_type", "status", "issued_on", "expires_on", "is_expired")
    list_filter = ("subject_type", "doc_type", "status")
    search_fields = ("code", "subject_code", "reference")
