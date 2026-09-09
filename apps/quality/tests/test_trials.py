"""Opening a trial, which the panel's button had no route for."""

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.quality.models import PilotTrial
from apps.registry.models import (
    Membership,
    OrganisationType,
    Party,
    Product,
    Role,
    User,
)


class TrialCreationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference", verbosity=0)
        party = Party.objects.create(
            code="ORG-5",
            legal_name="Lab",
            type=OrganisationType.objects.get(code="laboratory"),
            verification_status="verified",
        )
        cls.tech = User.objects.create(
            username="d.yusupov", display_name="D. Yusupov", status="active"
        )
        Membership.objects.create(
            user=cls.tech, party=party, role=Role.objects.get(code="lab_technician")
        )
        # `seed_reference` carries the role model, not the catalogue.
        cls.product = Product.objects.create(
            code="melon", name_uz="Qovun", name_ru="Дыня", name_en="Melon"
        )
        Product.objects.create(
            code="grape", name_uz="Uzum", name_ru="Виноград", name_en="Grape"
        )

    def open_trial(self, **body):
        access = self.client.post(
            reverse("registry:oneid"),
            {"persona": "d.yusupov"},
            content_type="application/json",
        ).json()["access"]
        return self.client.post(
            reverse("quality:trial-create"),
            {"product": "melon", "facility_code": "HUB-SMQ", **body},
            content_type="application/json",
            headers={"authorization": f"Bearer {access}"},
        )

    def test_a_trial_opens_planned_and_with_no_arms(self):
        """Which consignment gets divided is decided when there is one."""
        response = self.open_trial()

        self.assertEqual(response.status_code, 201, response.content[:200])
        trial = PilotTrial.objects.get()
        self.assertEqual(trial.status, "planned")
        self.assertEqual(trial.arms.count(), 0)
        self.assertEqual(trial.schedule_days, [0, 7, 14, 21, 28])

    def test_the_code_is_numbered_per_product(self):
        PilotTrial.objects.create(
            code="TR-MELON-01", product=self.product, schedule_days=[0]
        )

        self.assertEqual(self.open_trial().json()["code"], "TR-MELON-02")

    def test_another_product_starts_its_own_series(self):
        PilotTrial.objects.create(
            code="TR-MELON-01", product=self.product, schedule_days=[0]
        )

        self.assertEqual(
            self.open_trial(product="grape").json()["code"], "TR-GRAPE-01"
        )

    def test_a_schedule_may_be_given(self):
        response = self.open_trial(schedule_days=[0, 10, 20])

        self.assertEqual(PilotTrial.objects.get().schedule_days, [0, 10, 20])

    def test_an_unknown_product_is_refused(self):
        response = self.open_trial(product="durian")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(PilotTrial.objects.exists())
