"""Deployment checks that Django cannot know to make for us.

`manage.py check --deploy` is the gate both deployment scripts refuse to go
past, so a setting that would be catastrophic on a public host belongs here
rather than in a README nobody reads at 2am.
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register


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
                "ONEID_ADAPTER to a real adapter, or keep this deployment off "
                "the internet. Password sign-in keeps working either way."
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
    if settings.DEBUG:
        return []
    if not getattr(settings, "SEEDED_PASSWORDS_ROTATED", False):
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
