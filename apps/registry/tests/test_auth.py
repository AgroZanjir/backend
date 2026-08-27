"""Sessions: OneID at the front, JWT behind it, refresh in an httpOnly cookie."""

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.registry.models import Capability, Membership, OrganisationType, Party, Role, User


class AuthTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        view = Capability.objects.create(code="view", label_key="c_view")
        org_type = OrganisationType.objects.create(
            code="operator", label_key="ot_operator", icon="ops",
            required_checks=["identity"],
        )
        role = Role.objects.create(
            code="qc_inspector", label_key="r_qc", group_key="rg_hub", scope="facility"
        )
        role.capabilities.add(view)
        party = Party.objects.create(
            code="ORG-1", legal_name="Hub", type=org_type, verification_status="verified"
        )
        cls.user = User.objects.create(
            username="d.yusupov", display_name="D. Yusupov", status="active",
            oneid_verified=True,
        )
        Membership.objects.create(user=cls.user, party=party, role=role)

    def sign_in(self, persona="d.yusupov"):
        return self.client.post(
            reverse("registry:oneid"), {"persona": persona}, content_type="application/json"
        )

    def test_sign_in_returns_a_session_and_says_which_adapter_answered(self):
        response = self.sign_in()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["user"]["display_name"], "D. Yusupov")
        self.assertEqual(body["memberships"][0]["role"], "qc_inspector")
        self.assertEqual(body["memberships"][0]["capabilities"], ["view"])
        # A demo session must never be able to pass for a real one.
        self.assertEqual(body["adapter"], "stub")

    def test_the_refresh_token_is_an_httponly_cookie_the_client_cannot_read(self):
        response = self.sign_in()
        cookie = response.cookies["az_refresh"]

        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["path"], "/api/v1/auth/")
        self.assertNotIn("az_refresh", response.json())

    def test_refresh_exchanges_the_cookie_for_a_new_access_token(self):
        self.sign_in()
        response = self.client.post(reverse("registry:refresh"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.json())

    def test_refresh_without_a_cookie_is_401(self):
        self.assertEqual(self.client.post(reverse("registry:refresh")).status_code, 401)

    def test_logout_clears_the_cookie(self):
        self.sign_in()
        response = self.client.post(reverse("registry:logout"))

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.cookies["az_refresh"].value, "")

    def test_an_unknown_persona_is_refused(self):
        self.assertEqual(self.sign_in("nobody").status_code, 401)

    def test_a_suspended_account_cannot_sign_in(self):
        User.objects.filter(pk=self.user.pk).update(status="suspended")
        self.assertEqual(self.sign_in().status_code, 403)

    def test_me_needs_a_token(self):
        self.assertEqual(self.client.get(reverse("registry:me")).status_code, 401)

    def test_me_returns_the_session(self):
        access = self.sign_in().json()["access"]
        response = self.client.get(
            reverse("registry:me"), headers={"authorization": f"Bearer {access}"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["username"], "d.yusupov")


class UserStatusTests(TestCase):
    """Suspending an account, through the route the panel actually calls."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference", verbosity=0)
        party = Party.objects.create(
            code="ORG-9",
            legal_name="Platform",
            type=OrganisationType.objects.get(code="operator"),
            verification_status="verified",
        )
        cls.admin = User.objects.create(
            username="admin.one", display_name="A", status="active"
        )
        Membership.objects.create(
            user=cls.admin, party=party, role=Role.objects.get(code="platform_admin")
        )
        cls.victim = User.objects.create(
            username="someone", display_name="S", status="active"
        )
        Membership.objects.create(
            user=cls.victim, party=party, role=Role.objects.get(code="org_member")
        )

    def test_the_route_matches_an_integer_key(self):
        """`User` subclasses `AbstractUser`, whose primary key is an integer.

        The route declared a uuid converter, so every call 404'd - the panel's
        suspend button looked wired and was not.
        """
        access = self.client.post(
            reverse("registry:oneid"),
            {"persona": "admin.one"},
            content_type="application/json",
        ).json()["access"]

        response = self.client.post(
            reverse("registry:user-status", args=[self.victim.pk]),
            {"status": "suspended"},
            content_type="application/json",
            headers={"authorization": f"Bearer {access}"},
        )

        self.assertEqual(response.status_code, 200, response.content[:200])
        self.victim.refresh_from_db()
        self.assertEqual(self.victim.status, "suspended")
        self.assertFalse(self.victim.is_active)
