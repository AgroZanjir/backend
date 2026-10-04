"""Production boundaries that must hold before a release is made healthy."""

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from apps.common.checks import production_configuration, the_seeded_passwords_were_rotated
from apps.registry.auth import OneIDError, resolve_identity
from apps.registry.models import User


@override_settings(DEBUG=False, SEEDED_PASSWORDS_ROTATED=False)
class PilotPasswordChecks(TestCase):
    def test_empty_database_needs_no_rotation_assertion(self):
        self.assertEqual(the_seeded_passwords_were_rotated(None), [])

    def test_first_migration_does_not_require_a_user_table(self):
        with patch("apps.common.checks.connection.introspection.table_names", return_value=[]):
            self.assertEqual(the_seeded_passwords_were_rotated(None), [])

    def test_ordinary_administrator_is_not_a_pilot_account(self):
        User.objects.create_superuser("production-admin", password="test-password")
        self.assertEqual(the_seeded_passwords_were_rotated(None), [])

    def test_unusable_pilot_password_is_safe(self):
        user = User(username="az.owner")
        user.set_unusable_password()
        user.save()
        self.assertEqual(the_seeded_passwords_were_rotated(None), [])

    def test_pilot_password_requires_operator_verification(self):
        User.objects.create_user("az.owner", password="test-password")
        self.assertEqual(the_seeded_passwords_were_rotated(None)[0].id, "security.W102")

    def test_demo_persona_with_a_password_also_requires_verification(self):
        User.objects.create_user("n.sharipov", password="test-password")
        self.assertEqual(the_seeded_passwords_were_rotated(None)[0].id, "security.W102")

    @override_settings(SEEDED_PASSWORDS_ROTATED=True)
    def test_explicit_rotation_assertion_is_preserved(self):
        User.objects.create_user("az.owner", password="test-password")
        self.assertEqual(the_seeded_passwords_were_rotated(None), [])

    def test_database_errors_are_not_misreported_as_an_empty_database(self):
        with patch("apps.common.checks.connection.introspection.table_names", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                the_seeded_passwords_were_rotated(None)


@override_settings(
    DEBUG=False,
    SECRET_KEY="test-configuration-" + "0123456789abcdef" * 4,
    DATABASES={"default": {"ENGINE": "django.db.backends.postgresql"}},
    CACHES={"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache"}},
    ALLOWED_HOSTS=["agrozanjir.uz", "localhost"],
    CORS_ALLOWED_ORIGINS=[],
    CSRF_TRUSTED_ORIGINS=["https://agrozanjir.uz"],
    ONEID_ADAPTER="disabled",
    REFRESH_COOKIE={"secure": True},
)
class ProductionConfigurationChecks(SimpleTestCase):
    def test_same_origin_production_configuration_is_valid(self):
        self.assertEqual(production_configuration(None), [])

    def test_development_fallbacks_are_rejected(self):
        changes = [
            ({"DEBUG": True}, "security.E103"),
            ({"SECRET_KEY": "dev-only-not-for-production-do-not-deploy-with-this-key"}, "security.E104"),
            ({"DATABASES": {"default": {"ENGINE": "django.db.backends.sqlite3"}}}, "security.E105"),
            ({"CACHES": {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}}, "security.E106"),
            ({"ALLOWED_HOSTS": ["*"]}, "security.E107"),
            ({"CORS_ALLOWED_ORIGINS": ["http://example.com"]}, "security.E108"),
            ({"CSRF_TRUSTED_ORIGINS": []}, "security.E109"),
            ({"ONEID_ADAPTER": "stub"}, "security.E110"),
            ({"REFRESH_COOKIE": {"secure": False}}, "security.E111"),
        ]
        for settings, expected in changes:
            with self.subTest(expected=expected), override_settings(**settings):
                self.assertIn(expected, {error.id for error in production_configuration(None)})


class DisabledIdentityTests(SimpleTestCase):
    @override_settings(DEBUG=False, ONEID_ADAPTER="disabled")
    def test_disabled_identity_does_not_resolve_a_persona(self):
        with self.assertRaisesRegex(OneIDError, "username and password"):
            resolve_identity(persona="az.owner")
