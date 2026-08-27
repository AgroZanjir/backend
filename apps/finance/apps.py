from django.apps import AppConfig


class FinanceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.finance"
    label = "finance"

    def ready(self):
        """Register rule 3 with the lot spine.

        Encumbrance is an overlay, not a status: the spine asks every
        registered guard whether a lot may be dispatched, and this cluster
        answers. The arrow points this way on purpose - `lots` must be able to
        exist without `finance`, and this is how it does.
        """
        from apps.finance.models import dispatch_guard
        from apps.lots.models import register_dispatch_guard

        register_dispatch_guard(dispatch_guard)
