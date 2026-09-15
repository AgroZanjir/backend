"""Sending a sample, which records the report before it exists."""

from django.core.management import call_command
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.documents.models import Document
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


# Signs in through the demo door, which is the quick way to a session in a
# test. The production guard shuts that door when DEBUG is False - and the
# test runner sets DEBUG=False - so these opt back into it deliberately.
# The guard itself is tested in apps/common/tests/test_security.py.
@override_settings(DEBUG=True)
class LabRequestTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference", verbosity=0)
        party = Party.objects.create(
            code="ORG-H2",
            legal_name="Samarqand Hub",
            type=OrganisationType.objects.get(code="operator"),
            verification_status="verified",
        )
        cls.user = User.objects.create(
            username="q.lab", display_name="Q. Lab",
            status="active", oneid_verified=True,
        )
        Membership.objects.create(
            user=cls.user, party=party, role=Role.objects.get(code="qc_inspector")
        )
        product = Product.objects.create(
            code="melon", name_uz="Qovun", name_ru="Дыня", name_en="Melon"
        )
        farm = Farm.objects.create(code="F-2", party=party, name="Farm")
        cls.lot = Lot.objects.create(
            code="AZ-2026-TEST-0002",
            product=product,
            origin_farm=farm,
            owner_party=party,
            net_weight_g=1_000_000,
            gross_weight_g=1_050_000,
        )

    def setUp(self):
        # The sign-in throttles count in the cache, and the cache outlives a
        # test. Without this the fiftieth test to sign in is the one that
        # gets a 429 - a failure that moves around as tests are reordered.
        cache.clear()

    def send(self, **body):
        access = self.client.post(
            reverse("registry:oneid"),
            {"persona": "q.lab"},
            content_type="application/json",
        ).json()["access"]
        return self.client.post(
            reverse("quality:lab-request"),
            {"lot": self.lot.code, **body},
            content_type="application/json",
            headers={"authorization": f"Bearer {access}"},
        )

    def test_the_report_is_recorded_as_awaited(self):
        response = self.send(laboratory="Agro Lab")

        self.assertEqual(response.status_code, 201, response.content[:300])
        document = Document.objects.get()
        self.assertEqual(document.doc_type, Document.Type.LAB)
        self.assertEqual(document.status, Document.Status.PENDING)
        self.assertEqual(document.subject_code, self.lot.code)

    def test_the_lot_s_log_says_the_sample_went(self):
        self.send()

        event = self.lot.events.order_by("-sequence").first()
        self.assertEqual(event.event_type, "sample_sent")

    def test_a_second_request_does_not_leave_two_reports_awaited(self):
        """Two awaited rows is an export screen that cannot say which it waits
        for, and the sample is not analysed twice for having asked twice."""
        self.assertEqual(self.send().status_code, 201)

        response = self.send()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(Document.objects.count(), 1)

    def test_a_lot_that_does_not_exist_is_refused(self):
        access = self.client.post(
            reverse("registry:oneid"),
            {"persona": "q.lab"},
            content_type="application/json",
        ).json()["access"]
        response = self.client.post(
            reverse("quality:lab-request"),
            {"lot": "AZ-NOPE-0000"},
            content_type="application/json",
            headers={"authorization": f"Bearer {access}"},
        )

        self.assertEqual(response.status_code, 400)
