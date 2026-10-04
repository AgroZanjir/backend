"""Deployment checks that Django cannot know to make for us.

`manage.py check --deploy` is the gate both deployment scripts refuse to go
past, so a setting that would be catastrophic on a public host belongs here
rather than in a README nobody reads at 2am.
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register
from django.db import connection


# `deploy=True`: these run under `check --deploy` and not on every `check`.
# The test runner sets DEBUG=False, so without it every test run would fail
# on a configuration that is correct for a test run.
@register(Tags.security, deploy=True)
def the_demo_identity_door_is_shut(app_configs, **kwargs):
    """The stub identity adapter must never answer on a public host.

    `ONEID_ADAPTER=stub` resolves a sign-in from a *username alone* - it is
    the demo door, and it is how the panels are browsed before OneID exists.
    On the open internet it is a complete authentication bypass: anybody who
    can reach the API can become anybody, including the platform owner.

    Left as an error rather than a warning because there is no deployment in
    which this is acceptable, and because both scripts run this check with
    `--fail-level WARNING` and stop.
    """
    if settings.DEBUG:
        return []
    if getattr(settings, "ONEID_ADAPTER", "stub") != "stub":
        return []
    return [
        Error(
            "ONEID_ADAPTER=stub with DEBUG=False: the demo sign-in door is "
            "open on a public host.",
            hint=(
                "The stub resolves a session from a username with no proof of "
                "identity, and /auth/personas/ lists the usernames. Set "
                "ONEID_ADAPTER=disabled. Password sign-in keeps working."
            ),
            id="security.E101",
        )
    ]


@register(Tags.security, deploy=True)
def the_seeded_passwords_were_rotated(app_configs, **kwargs):
    """A published password is not a password.

    The pilot's accounts are seeded with printed passwords so a demonstration
    can be given. They are in chat logs, in slide decks and in whatever was
    emailed round; on a public host they are all public.
    """
    if settings.DEBUG or getattr(settings, "SEEDED_PASSWORDS_ROTATED", False):
        return []
    from apps.common.management.commands.seed_accounts import ACCOUNTS
    from apps.common.management.commands.seed_demo import USERS
    from apps.registry.models import User

    # First installation runs this before migrations. Do not hide connection
    # errors: only the verified absence of this table is safe to skip.
    if User._meta.db_table not in connection.introspection.table_names():
        return []
    usernames = {account[0] for _, accounts in ACCOUNTS for account in accounts}
    usernames.update(account[0] for account in USERS)
    seeded = User.objects.filter(username__in=usernames).only("password")
    if any(user.has_usable_password() for user in seeded.iterator()):
        return [
            Warning(
                "The seeded demonstration passwords may not have been rotated.",
                hint=(
                    "Run `manage.py seed_accounts --rotate` and set "
                    "SEEDED_PASSWORDS_ROTATED=True once the new list is stored "
                    "somewhere that is not a chat log."
                ),
                id="security.W102",
            )
        ]
    return []


@register(Tags.security, deploy=True)
def production_configuration(app_configs, **kwargs):
    """Fail before migrations when production would use development fallbacks."""
    errors = []

    def require(condition, message, number):
        if not condition:
            errors.append(Error(message, id=f"security.E{number}"))

    require(not settings.DEBUG, "DEBUG must be False for deployment.", 103)
    secret = settings.SECRET_KEY
    require(
        len(secret) >= 50
        and len(set(secret)) >= 5
        and not secret.startswith(("django-insecure-", "dev-only-", "ci-only-", "static-build-")),
        "Set a unique random DJANGO_SECRET_KEY of at least 50 characters.",
        104,
    )
    require(
        settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql",
        "Production DATABASE_URL must use PostgreSQL.",
        105,
    )
    require(
        settings.CACHES["default"]["BACKEND"] == "django.core.cache.backends.redis.RedisCache",
        "Production CACHE_URL must use a shared Redis cache.",
        106,
    )
    hosts = settings.ALLOWED_HOSTS
    require(
        "*" not in hosts and any(host not in {"localhost", "127.0.0.1", "[::1]"} for host in hosts),
        "ALLOWED_HOSTS must include the explicit production hostname without '*'.",
        107,
    )
    for name in ("CORS_ALLOWED_ORIGINS", "CSRF_TRUSTED_ORIGINS"):
        origins = getattr(settings, name)
        require(
            (bool(origins) or name == "CORS_ALLOWED_ORIGINS")
            and all(origin.startswith("https://") and "*" not in origin for origin in origins),
            f"{name} must contain explicit HTTPS origins.",
            108 if name == "CORS_ALLOWED_ORIGINS" else 109,
        )
    require(
        settings.ONEID_ADAPTER == "disabled",
        "ONEID_ADAPTER must be disabled until a real integration exists.",
        110,
    )
    require(
        settings.REFRESH_COOKIE["secure"],
        "REFRESH_COOKIE_SECURE must be True in production.",
        111,
    )
    return errors
