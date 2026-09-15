"""The holes, and that they are shut.

Each test here is a thing somebody could do to this platform from the open
internet before the change it accompanies. They are written as the attack,
not as the feature, because that is what stops them being "simplified" back
out by somebody who reads only the assertion.
"""

from unittest.mock import patch

from django.core.cache import cache
from django.core.checks import Error
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.throttling import SimpleRateThrottle

from apps.common.checks import the_demo_identity_door_is_shut
from apps.lots.models import Lot
from apps.registry.models import (
    Farm,
    Membership,
    OrganisationType,
    Party,
    Product,
    Role,
    User,
)


class Fixture(TestCase):
    """Two organisations that have nothing to do with each other."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference", verbosity=0)
        farmer = OrganisationType.objects.get(code="farmer")
        cls.product = Product.objects.create(
            code="melon", name_uz="Qovun", name_ru="Дыня", name_en="Melon"
        )

        cls.mine = Party.objects.create(
            code="ORG-M", legal_name="My farm", type=farmer,
            verification_status="verified",
        )
        cls.theirs = Party.objects.create(
            code="ORG-T", legal_name="Their farm", type=farmer,
            verification_status="verified",
        )
        # Two attackers, because the capability check runs before the scoping
        # one and would otherwise answer first: a farm manager holds `capture`
        # and `transact`, an inspector holds `approve`. Each is used against
        # the endpoint their role can actually reach, so what refuses them is
        # the scoping and never the capability.
        cls.me = cls.person("me", cls.mine, "farm_manager")
        cls.inspector = cls.person("inspector", cls.mine, "qc_inspector")
        cls.lot = cls.make_lot("AZ-2026-THEIRS-0001", cls.theirs)

    @classmethod
    def person(cls, username, party, role):
        user = User.objects.create(
            username=username, display_name=username.title(),
            status="active", oneid_verified=True,
        )
        # A real password, because the demo door is shut under test: the test
        # runner sets DEBUG=False, which is exactly the condition the guard
        # refuses under. Signing in the way a deployment does is the point.
        user.set_password("correct-horse-battery-staple")
        user.save(update_fields=["password"])
        Membership.objects.create(
            user=user, party=party, role=Role.objects.get(code=role)
        )
        return user

    @classmethod
    def make_lot(cls, code, party):
        return Lot.objects.create(
            code=code,
            product=cls.product,
            origin_farm=Farm.objects.create(code=f"F-{code}", party=party, name="Farm"),
            owner_party=party,
            net_weight_g=1_000_000,
            gross_weight_g=1_050_000,
        )

    def setUp(self):
        cache.clear()

    def token(self, username="me"):
        response = self.client.post(
            reverse("registry:password"),
            {"username": username, "password": "correct-horse-battery-staple"},
            content_type="application/json",
        )
        return response.json()["access"]

    def post(self, url, body=None, username="me"):
        return self.client.post(
            url,
            body or {},
            content_type="application/json",
            headers={"authorization": f"Bearer {self.token(username)}"},
        )


class CrossOrganisationWriteTests(Fixture):
    """Holding a capability is not holding it over everybody's records."""

    def test_a_stranger_cannot_write_off_another_farm_s_harvest(self):
        response = self.post(
            reverse("lots:write-off", args=[self.lot.code]),
            {"reason": "spoiled"},
            username="inspector",
        )

        self.assertEqual(response.status_code, 404, response.content[:200])
        self.lot.refresh_from_db()
        self.assertNotEqual(self.lot.status, Lot.Status.WRITTEN_OFF)

    def test_a_stranger_cannot_grade_another_farm_s_lot(self):
        response = self.post(
            reverse("lots:grade", args=[self.lot.code]),
            {"grade": "A", "defect_pct": "0"},
            username="inspector",
        )

        self.assertEqual(response.status_code, 404)

    def test_a_stranger_cannot_reserve_another_farm_s_lot(self):
        response = self.post(reverse("lots:reserve", args=[self.lot.code]))

        self.assertEqual(response.status_code, 404)

    def test_a_stranger_cannot_inspect_another_farm_s_lot(self):
        """A QC record can move the lot's grade, so it is a write on it."""
        response = self.post(
            reverse("quality:qc-create"),
            {
                "lot": self.lot.code,
                "stage": "intake",
                "inspected_on": "2026-09-15",
                "grade_assigned": "C",
            },
        )

        self.assertEqual(response.status_code, 404)

    def test_the_refusal_does_not_say_whether_the_lot_exists(self):
        """Otherwise the 404/403 difference enumerates every lot code."""
        real = self.post(reverse("lots:reserve", args=[self.lot.code]))
        invented = self.post(reverse("lots:reserve", args=["AZ-2026-NOPE-9999"]))

        self.assertEqual(real.status_code, invented.status_code)
        self.assertEqual(real.json(), invented.json())

    def test_my_own_lot_is_still_mine_to_act_on(self):
        """The guard has to let the ordinary case through - a scoping rule
        that refuses everybody passes every test above and ships nothing."""
        mine = self.make_lot("AZ-2026-MINE-0001", self.mine)
        # Reserving follows grading in the lifecycle.
        mine.transition(Lot.Status.GRADED)

        response = self.post(reverse("lots:reserve", args=[mine.code]))

        self.assertEqual(response.status_code, 200, response.content[:200])


class DemoDoorTests(Fixture):
    """`ONEID_ADAPTER=stub` signs anybody in as anybody, from a username."""

    @override_settings(DEBUG=False)
    def test_the_demo_sign_in_refuses_on_a_public_host(self):
        response = self.client.post(
            reverse("registry:oneid"),
            {"persona": "me"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("access", response.json())

    @override_settings(DEBUG=False)
    def test_the_persona_list_is_not_a_staff_directory(self):
        response = self.client.get(reverse("registry:personas"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["personas"], [])

    @override_settings(DEBUG=False)
    def test_the_deployment_check_refuses_the_configuration(self):
        """Both deployment scripts gate on `check --deploy`."""
        with override_settings(ONEID_ADAPTER="stub"):
            problems = the_demo_identity_door_is_shut(None)

        self.assertEqual(len(problems), 1)
        self.assertIsInstance(problems[0], Error)
        self.assertEqual(problems[0].id, "security.E101")

    @override_settings(DEBUG=True)
    def test_it_stays_open_where_it_is_meant_to_be(self):
        """The panels have to be browsable before OneID exists."""
        response = self.client.post(
            reverse("registry:oneid"),
            {"persona": "me"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)


class SignInThrottleTests(Fixture):
    """An unbounded sign-in is a password guesser's whole afternoon."""

    def attempt(self, username="me", password="wrong"):
        return self.client.post(
            reverse("registry:password"),
            {"username": username, "password": password},
            content_type="application/json",
        )

    def test_guessing_from_one_address_is_cut_off(self):
        with patch.dict(SimpleRateThrottle.THROTTLE_RATES, {"signin-address": "3/min"}):
            for _ in range(3):
                self.assertIn(self.attempt().status_code, (400, 401))

            self.assertEqual(self.attempt().status_code, 429)

    def test_guessing_at_one_account_is_cut_off_across_addresses(self):
        """A botnet gives every request a new address, so the address limit
        never fires and only the per-account one is left."""
        with patch.dict(
            SimpleRateThrottle.THROTTLE_RATES,
            {"signin-address": "1000/min", "signin-account": "3/hour"},
        ):
            for n in range(3):
                self.attempt()

            response = self.client.post(
                reverse("registry:password"),
                {"username": "me", "password": "wrong"},
                content_type="application/json",
                REMOTE_ADDR="203.0.113.77",
            )

        self.assertEqual(response.status_code, 429)

    def test_an_invented_username_is_counted_too(self):
        """Counting only real accounts answers a guess at a real name
        differently from a guess at an invented one, which is how an attacker
        learns which names are real."""
        with patch.dict(SimpleRateThrottle.THROTTLE_RATES, {"signin-account": "2/hour"}):
            self.attempt(username="nobody.at.all")
            self.attempt(username="nobody.at.all")

            self.assertEqual(self.attempt(username="nobody.at.all").status_code, 429)
