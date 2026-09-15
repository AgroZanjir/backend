from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.common"
    label = "common"

    def ready(self):
        # Registers the deployment checks. Imported for the side effect, which
        # is the only way Django's check framework collects them.
        from apps.common import checks  # noqa: F401
