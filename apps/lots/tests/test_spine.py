"""The four rules of the spine, as tests.

If any of these fail the platform has stopped being a system of record, so
they are written against behaviour rather than implementation: append and read
back, pledge and try to dispatch, split and trace the parents.
"""

from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.finance.models import Encumbrance, FinanceApplication
from apps.lots.models import Lot, LotEvent, LotRelation, dispatch_blockers
from apps.registry.models import Farm, OrganisationType, Party, Product


class SpineTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        farmer = OrganisationType.objects.create(
            code="farmer", label_key="ot_farmer", icon="harvest",
            required_checks=["identity"],
        )
        bank_type = OrganisationType.objects.create(
            code="bank", label_key="ot_bank", icon="port", required_checks=["identity"],
        )
        cls.party = Party.objects.create(
            code="ORG-1", legal_name="Test farm", type=farmer
        )
        cls.bank = Party.objects.create(
            code="ORG-2", legal_name="Test bank", type=bank_type
        )
        cls.product = Product.objects.create(
            code="melon", name_uz="Qovun", name_ru="Дыня", name_en="Melon"
        )
        cls.farm = Farm.objects.create(
            code="F-1", party=cls.party, name="Test farm"
        )

    def make_lot(self, **kwargs) -> Lot:
        defaults = {
            "code": "AZ-2026-TST-0001",
            "product": self.product,
            "origin_farm": self.farm,
            "owner_party": self.party,
            "net_weight_g": 4_200_000,
            "harvested_on": date(2026, 8, 14),
        }
        return Lot.objects.create(**{**defaults, **kwargs})


class EventLogTests(SpineTestCase):
    """Rule 2: the log is the truth, and it is append-only."""

    def test_events_chain_to_one_another(self):
        lot = self.make_lot()
        first = lot.log("registered", payload={"net_weight_g": 4_200_000})
        second = lot.log("graded", payload={"grade": "A"})

        self.assertEqual(first.prev_hash, "")
        self.assertEqual(second.prev_hash, first.hash)
        self.assertTrue(lot.chain_intact)

    def test_an_event_cannot_be_edited(self):
        lot = self.make_lot()
        event = lot.log("registered")

        event.payload = {"net_weight_g": 9_000_000}
        with self.assertRaises(ValidationError):
            event.save()

    def test_an_event_cannot_be_deleted(self):
        lot = self.make_lot()
        with self.assertRaises(ValidationError):
            lot.log("registered").delete()

    def test_tampering_with_history_breaks_the_chain(self):
        """The point of the hash: an edit that dodges `save()` is still visible."""
        lot = self.make_lot()
        lot.log("registered", payload={"net_weight_g": 4_200_000})
        lot.log("graded", payload={"grade": "A"})

        # Straight to SQL, as an intruder with database access would.
        LotEvent.objects.filter(lot=lot, sequence=1).update(
            payload={"net_weight_g": 9_000_000}
        )

        self.assertFalse(lot.chain_intact)

    def test_the_chain_survives_a_round_trip_through_the_database(self):
        """Timestamps are normalised to UTC before hashing; this proves it."""
        lot = self.make_lot()
        lot.log("registered")
        lot.log("placed")

        self.assertTrue(Lot.objects.get(pk=lot.pk).chain_intact)


class LifecycleTests(SpineTestCase):
    def test_a_lot_cannot_skip_the_lifecycle(self):
        lot = self.make_lot()
        with self.assertRaises(ValidationError):
            lot.transition(Lot.Status.SETTLED)

    def test_the_permitted_path_works(self):
        lot = self.make_lot()
        lot.transition(Lot.Status.GRADED)
        lot.transition(Lot.Status.STORED)
        lot.transition(Lot.Status.DISPATCHED)
        self.assertEqual(lot.status, Lot.Status.DISPATCHED)


class EncumbranceOverlayTests(SpineTestCase):
    """Rule 3: a lien is an overlay, not a status."""

    def pledge(self, lot) -> Encumbrance:
        application = FinanceApplication.objects.create(
            code="FA-1",
            applicant_party=self.party,
            lender_party=self.bank,
            amount_minor=100,
        )
        return Encumbrance.objects.create(
            lot=lot, application=application, holder_party=self.bank, amount_minor=100
        )

    def test_a_pledged_lot_keeps_its_status(self):
        lot = self.make_lot(status=Lot.Status.STORED)
        self.pledge(lot)

        lot.refresh_from_db()
        self.assertEqual(lot.status, Lot.Status.STORED)
        self.assertNotIn("pledged", dict(Lot.Status.choices))

    def test_a_lien_blocks_dispatch_and_names_the_lender(self):
        lot = self.make_lot(status=Lot.Status.STORED)
        self.pledge(lot)

        blockers = dispatch_blockers(lot)
        self.assertEqual(len(blockers), 1)
        self.assertIn("Test bank", blockers[0])

        with self.assertRaises(ValidationError):
            lot.transition(Lot.Status.DISPATCHED)

    def test_release_is_an_event_not_a_deletion(self):
        lot = self.make_lot(status=Lot.Status.STORED)
        lien = self.pledge(lot)

        lien.release(reference="TEST-1")

        self.assertTrue(Encumbrance.objects.filter(pk=lien.pk).exists())
        self.assertIsNotNone(lien.released_at)
        self.assertEqual(dispatch_blockers(lot), [])
        self.assertTrue(lot.events.filter(event_type="lien_released").exists())
        lot.transition(Lot.Status.DISPATCHED)


class RelationTests(SpineTestCase):
    """Rule 1: lots split and merge, and a child traces back to its parents."""

    def test_a_split_child_traces_back_to_the_parent_farm(self):
        parent = self.make_lot(net_weight_g=10_000_000)
        child = self.make_lot(code="AZ-2026-TST-0002", net_weight_g=6_000_000)
        LotRelation.objects.create(
            parent=parent, child=child, kind=LotRelation.Kind.SPLIT,
            quantity_g=6_000_000,
        )

        parents = [relation.parent for relation in child.parents.all()]
        self.assertEqual(parents, [parent])
        self.assertEqual(parents[0].origin_farm, self.farm)

    def test_a_lot_cannot_be_its_own_parent(self):
        lot = self.make_lot()
        with self.assertRaises(Exception):
            LotRelation.objects.create(
                parent=lot, child=lot, kind=LotRelation.Kind.SPLIT, quantity_g=1
            )
