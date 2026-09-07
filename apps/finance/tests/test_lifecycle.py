"""The whole life of a credit file, and the ways it may not be lived.

The screens only ever offered approve and reject, so `review`, `disbursed`
and `repaid` existed in the model and in the seeded data with no code path
that could reach them.
"""

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.finance.models import FinanceApplication
from apps.registry.models import Membership, OrganisationType, Party, Role, User


class LifecycleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference", verbosity=0)
        bank_type = OrganisationType.objects.get(code="bank")
        farm_type = OrganisationType.objects.get(code="farmer")
        cls.bank = Party.objects.create(
            code="ORG-B1", legal_name="Agrobank", type=bank_type,
            verification_status="verified",
        )
        cls.other_bank = Party.objects.create(
            code="ORG-B2", legal_name="Ipoteka Bank", type=bank_type,
            verification_status="verified",
        )
        cls.farm = Party.objects.create(
            code="ORG-F1", legal_name="Nodir", type=farm_type,
            verification_status="verified",
        )
        cls.approver = cls.person("approver", cls.bank, "credit_approver")
        cls.officer = cls.person("officer", cls.bank, "credit_officer")
        cls.rival = cls.person("rival", cls.other_bank, "credit_approver")

    @classmethod
    def person(cls, username, party, role):
        user = User.objects.create(
            username=username, display_name=username.title(),
            status="active", oneid_verified=True,
        )
        Membership.objects.create(
            user=user, party=party, role=Role.objects.get(code=role)
        )
        return user

    def setUp(self):
        self.application = FinanceApplication.objects.create(
            code="FA-T-0001",
            applicant_party=self.farm,
            lender_party=self.bank,
            kind="inventory",
            amount_minor=100_000_000,
            currency="UZS",
            status=FinanceApplication.Status.SUBMITTED,
        )

    def token(self, username):
        return self.client.post(
            reverse("registry:oneid"),
            {"persona": username},
            content_type="application/json",
        ).json()["access"]

    def act(self, step, username, body=None):
        return self.client.post(
            reverse(f"finance:application-{step}", args=[self.application.code]),
            body or {},
            content_type="application/json",
            headers={"authorization": f"Bearer {self.token(username)}"},
        )

    def status_now(self):
        self.application.refresh_from_db()
        return self.application.status

    # -- the path all the way through -------------------------------------

    def test_submitted_through_to_repaid(self):
        self.assertEqual(self.act("review", "officer").status_code, 200)
        self.assertEqual(self.status_now(), "review")

        self.assertEqual(
            self.act("decide", "approver", {"status": "approved"}).status_code, 200
        )
        self.assertEqual(self.status_now(), "approved")
        self.assertIsNotNone(self.application.decided_on)

        self.assertEqual(self.act("disburse", "officer").status_code, 200)
        self.assertEqual(self.status_now(), "disbursed")

        self.assertEqual(self.act("repay", "officer").status_code, 200)
        self.assertEqual(self.status_now(), "repaid")

    def test_a_small_file_can_be_approved_off_the_queue(self):
        """Review is a step, not a toll gate."""
        self.assertEqual(
            self.act("decide", "approver", {"status": "approved"}).status_code, 200
        )
        self.assertEqual(self.status_now(), "approved")

    # -- and the ways it may not go ---------------------------------------

    def test_money_cannot_move_before_the_decision(self):
        response = self.act("disburse", "officer")

        self.assertEqual(response.status_code, 409)
        self.assertIn("not a permitted transition", response.json()["blockers"][0])
        self.assertEqual(self.status_now(), "submitted")

    def test_nothing_follows_a_rejection(self):
        self.act("decide", "approver", {"status": "rejected"})

        self.assertEqual(self.act("disburse", "officer").status_code, 409)
        self.assertEqual(self.status_now(), "rejected")

    def test_a_decision_is_approve_or_reject_and_nothing_else(self):
        """The endpoint accepted all seven, which made it a way to write
        `repaid` onto a file nobody had answered."""
        response = self.act("decide", "approver", {"status": "repaid"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.status_now(), "submitted")

    def test_an_approver_at_another_bank_is_refused(self):
        """`decide` says the role may decide; it does not say whose."""
        response = self.act("decide", "rival", {"status": "approved"})

        self.assertEqual(response.status_code, 403)
        self.assertIn("another lender", response.json()["blockers"][0])
        self.assertEqual(self.status_now(), "submitted")

    def test_the_officer_cannot_decide_and_the_approver_need_not_settle(self):
        self.assertEqual(
            self.act("decide", "officer", {"status": "approved"}).status_code, 403
        )
        self.assertEqual(self.status_now(), "submitted")

    def test_the_admin_can_still_correct_a_state_the_product_cannot_reach(self):
        """`force` is why the manual adapter surface exists."""
        self.application.transition("repaid", force=True)

        self.assertEqual(self.status_now(), "repaid")
