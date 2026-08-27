from django.contrib import admin

from apps.quality.models import PilotTrial, QcRecord, TrialArm, TrialObservation


@admin.register(QcRecord)
class QcRecordAdmin(admin.ModelAdmin):
    list_display = ("lot", "stage", "inspected_on", "grade_assigned", "defect_pct", "passed")
    list_filter = ("stage", "grade_assigned", "passed")
    search_fields = ("lot__code",)
    autocomplete_fields = ("lot", "inspector")


class TrialArmInline(admin.TabularInline):
    model = TrialArm
    extra = 0
    autocomplete_fields = ("lot",)


@admin.register(PilotTrial)
class PilotTrialAdmin(admin.ModelAdmin):
    list_display = ("code", "product", "status", "started_on", "facility_code", "observed_points")
    list_filter = ("status", "product")
    search_fields = ("code",)
    inlines = (TrialArmInline,)


@admin.register(TrialObservation)
class TrialObservationAdmin(admin.ModelAdmin):
    list_display = ("arm", "day_index", "observed_on", "weight_loss_pct", "waste_pct", "firmness_n")
    list_filter = ("arm__trial", "arm__kind")
