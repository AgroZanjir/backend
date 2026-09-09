"""The panels' assistant: what it may see, and what it writes down for having seen it.

The public assistant's tests care that it cannot reach past the public
passport. These care about the mirror image - that it reaches exactly as far as
the operator's own screens do and not one row further, and that opening a
passport through a question leaves the same line in the audit log as opening
one through the panel.

Two organisations with a lot each, and a bank with a lien over one of them.
That is the smallest arrangement in which "scoped" and "not scoped" look
different, and every test below is a version of the same question: who sees
whose lot.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.assistant import brief, client, operator_tools
from apps.finance.models import Encumbrance, FinanceApplication
from apps.governance.models import AuditEntry
from apps.lots.models import Lot
from apps.registry.models import (
    Capability,
    Membership,
    OrganisationType,
    Party,
    Product,
    Role,
    User,
)
from apps.storage.models import Facility, StorageZone


def _request(user):
    """The thinnest thing `audit` and the tools need: a caller."""
    return SimpleNamespace(user=user)


class OperatorFixture(TestCase):
    def setUp(self):
        farm_type = OrganisationType.objects.create(code="farm", label_key="ot_farm")
        bank_type = OrganisationType.objects.create(code="bank", label_key="ot_bank")

        view = Capability.objects.create(code="view", label_key="c_view")
        self.farmer_role = Role.objects.create(
            code="producer", label_key="r_prod", group_key="g_prod", scope="org"
        )
        self.farmer_role.capabilities.add(view)
        self.bank_role = Role.objects.create(
            code="credit_officer", label_key="r_credit", group_key="g_fin", scope="org"
        )
        self.bank_role.capabilities.add(view)
        self.platform_role = Role.objects.create(
            code="auditor", label_key="r_audit", group_key="g_plat", scope="platform"
        )
        self.platform_role.capabilities.add(view)

        self.zarafshon = Party.objects.create(
            code="P-FARM-1", legal_name="Zarafshon Dehqon MChJ", type=farm_type
        )
        self.rival = Party.objects.create(
            code="P-FARM-2", legal_name="Xorazm Bog‘i MChJ", type=farm_type
        )
        self.bank = Party.objects.create(
            code="P-BANK-1", legal_name="Agrobank ATB", type=bank_type
        )

        self.product = Product.objects.create(
            code="melon", name_uz="Qovun", name_ru="Дыня", name_en="Melon"
        )

        self.mine = Lot.objects.create(
            code="AZ-2026-SMQ-0001",
            product=self.product,
            owner_party=self.zarafshon,
            harvested_on=date(2026, 8, 1),
            sell_by=date(2026, 8, 10),
            net_weight_g=4_200_000,
            valuation_minor=120_000_000,
        )
        self.theirs = Lot.objects.create(
            code="AZ-2026-XRZ-0002",
            product=self.product,
            owner_party=self.rival,
            harvested_on=date(2026, 8, 2),
            net_weight_g=3_100_000,
        )

        application = FinanceApplication.objects.create(
            code="FA-0001",
            applicant_party=self.zarafshon,
            lender_party=self.bank,
            amount_minor=50_000_000,
            currency="UZS",
        )
        self.lien = Encumbrance.objects.create(
            lot=self.mine,
            application=application,
            holder_party=self.bank,
            amount_minor=50_000_000,
            currency="UZS",
        )

        facility = self.facility = Facility.objects.create(
            code="F-SMQ-1", name="Samarqand hub", operator_party=self.zarafshon
        )
        self.other_facility = Facility.objects.create(
            code="F-XRZ-1", name="Xorazm hub", operator_party=self.rival
        )
        StorageZone.objects.create(
            code="Z-ZEROCO-01",
            facility=facility,
            mode="zeroco",
            capacity_g=20_000_000,
            target_temp_c=0,
            target_rh_pct=92,
        )
        StorageZone.objects.create(
            code="Z-XRZ-01",
            facility=self.other_facility,
            mode="cold",
            capacity_g=20_000_000,
            target_temp_c=4,
            target_rh_pct=90,
        )

        self.farmer = self._person("aziz", self.zarafshon, self.farmer_role)
        self.officer = self._person("dilnoza", self.bank, self.bank_role)
        self.auditor = self._person("platform", self.zarafshon, self.platform_role)

    def _person(self, username: str, party: Party, role: Role) -> User:
        # No password: nothing here signs in with one, and hashing three per
        # test put a minute and a half on the suite.
        user = User.objects.create(username=username, display_name=username.title())
        Membership.objects.create(user=user, party=party, role=role)
        return user


class ScopeTests(OperatorFixture):
    def test_a_producer_sees_their_own_lots_and_not_their_neighbour_s(self):
        result = operator_tools.run(
            _request(self.farmer),
            "find_lots",
            {"status": "", "product": "", "zone": "", "pledged": "", "expiring_within_days": 0},
        )

        self.assertEqual([row["code"] for row in result["results"]], [self.mine.code])
        self.assertEqual(result["total"], 1)
        self.assertNotIn(self.rival.legal_name, json.dumps(result, default=str))

    def test_a_lender_sees_the_lot_it_has_a_lien_over_and_no_other(self):
        """The bank has no ownership and no facility - the lien is the whole route."""
        result = operator_tools.run(
            _request(self.officer),
            "find_lots",
            {"status": "", "product": "", "zone": "", "pledged": "", "expiring_within_days": 0},
        )

        self.assertEqual([row["code"] for row in result["results"]], [self.mine.code])

    def test_a_platform_role_sees_every_organisation_s_lots(self):
        result = operator_tools.run(
            _request(self.auditor),
            "find_lots",
            {"status": "", "product": "", "zone": "", "pledged": "", "expiring_within_days": 0},
        )

        self.assertEqual(
            sorted(row["code"] for row in result["results"]),
            [self.mine.code, self.theirs.code],
        )

    def test_a_passport_for_somebody_else_s_lot_is_not_found_rather_than_refused(self):
        # Not "forbidden": telling them the code exists but is not theirs is
        # itself a disclosure. The panel view answers the same way.
        result = operator_tools.run(
            _request(self.farmer), "lot_passport", {"code": self.theirs.code}
        )

        self.assertEqual(result["error"], "not_found")

    def test_zones_are_narrowed_to_the_facilities_the_caller_operates(self):
        result = operator_tools.run(_request(self.farmer), "find_zones", {"facility": ""})

        self.assertEqual([z["code"] for z in result["results"]], ["Z-ZEROCO-01"])

    def test_liens_are_narrowed_to_lots_the_caller_can_already_see(self):
        """Deliberately tighter than the lien register screen, which is not scoped."""
        mine = operator_tools.run(_request(self.farmer), "find_liens", {"active_only": "yes"})
        theirs = Encumbrance.objects.filter(lot=self.theirs)

        self.assertEqual([lien["lot"] for lien in mine["results"]], [self.mine.code])
        self.assertFalse(theirs.exists())

        # And the lender sees its own book through the same tool.
        held = operator_tools.run(_request(self.officer), "find_liens", {"active_only": "yes"})
        self.assertEqual([lien["lot"] for lien in held["results"]], [self.mine.code])


class FilterTests(OperatorFixture):
    def _find(self, **overrides):
        payload = {
            "status": "",
            "product": "",
            "zone": "",
            "pledged": "",
            "expiring_within_days": 0,
        }
        payload.update(overrides)
        return operator_tools.run(_request(self.auditor), "find_lots", payload)

    def test_pledged_selects_and_excludes_by_the_overlay_never_by_a_status(self):
        pledged = self._find(pledged="yes")
        free = self._find(pledged="no")

        self.assertEqual([r["code"] for r in pledged["results"]], [self.mine.code])
        self.assertEqual([r["code"] for r in free["results"]], [self.theirs.code])
        # Rule 3: the pledged lot's status is untouched by the lien.
        self.assertEqual(pledged["results"][0]["status"], "registered")
        self.assertTrue(pledged["results"][0]["pledged"])

    def test_a_lot_that_already_expired_is_not_one_that_is_about_to(self):
        """The window had no floor, so "expiring this week" answered with a
        lot whose date passed a week ago - which for a hub manager is the
        difference between something to sell and something to write off."""
        gone = self.mine
        gone.sell_by = timezone.localdate() - timedelta(days=7)
        gone.save(update_fields=["sell_by"])

        self.assertEqual(self._find(expiring_within_days=7)["results"], [])

    def test_the_expired_ones_can_still_be_asked_for(self):
        gone = self.mine
        gone.sell_by = timezone.localdate() - timedelta(days=7)
        gone.save(update_fields=["sell_by"])
        alive = self.theirs
        alive.sell_by = timezone.localdate() + timedelta(days=3)
        alive.save(update_fields=["sell_by"])

        self.assertEqual(
            [r["code"] for r in self._find(expired="yes")["results"]], [gone.code]
        )

    def test_expiring_within_days_counts_from_today_not_from_the_dataset(self):
        soon = self.mine
        soon.sell_by = timezone.localdate() + timedelta(days=3)
        soon.save(update_fields=["sell_by"])

        self.assertEqual(
            [r["code"] for r in self._find(expiring_within_days=7)["results"]],
            [soon.code],
        )
        # A lot with no sell-by must never be swept in by a date filter.
        self.assertNotIn(
            self.theirs.code,
            [r["code"] for r in self._find(expiring_within_days=3650)["results"]],
        )

    def test_a_filter_value_that_names_nothing_is_an_error_not_an_empty_result(self):
        """The bug this replaces told a producer with two lots that he had none.

        A malformed `status` arrived from the model, matched no row, and came
        back as `total: 0` - which reads exactly like a true empty result. The
        answer was a confident paragraph saying he had no lots at all. An
        error the model can read is the only outcome that cannot become a
        falsehood.
        """
        result = self._find(status="teleported")

        self.assertEqual(result["error"], "bad_filter")
        self.assertIn("teleported", result["detail"])
        # It says what would have worked, so the model's next call is right.
        self.assertIn("stored", result["detail"])

    def test_every_filter_is_checked_against_what_exists(self):
        for field, value in [
            ("product", "durian"),
            ("zone", "Z-NOWHERE"),
            ("pledged", "maybe"),
        ]:
            with self.subTest(field=field):
                self.assertEqual(self._find(**{field: value})["error"], "bad_filter")

        self.assertEqual(
            operator_tools.run(_request(self.farmer), "find_zones", {"facility": "F-NONE"})[
                "error"
            ],
            "bad_filter",
        )

    def test_an_omitted_filter_is_no_filter(self):
        """Every filter is optional in the schema, so absent must mean absent.

        Passing nothing at all has to behave exactly like passing empties - it
        is what the model does now that it is no longer forced to invent a
        value for a filter it does not want.
        """
        bare = operator_tools.run(_request(self.auditor), "find_lots", {})

        self.assertEqual(bare["total"], 2)
        self.assertEqual(bare["filters_applied"], {})

    def test_the_filters_that_were_applied_come_back_with_the_rows(self):
        # An empty result reads very differently beside the filters that
        # produced it: "none stored" is an answer, "none" is a falsehood.
        result = self._find(status="stored", pledged="yes")

        self.assertEqual(
            result["filters_applied"], {"status": ["stored"], "pledged": "yes"}
        )

    def test_a_result_that_was_cut_off_says_so(self):
        with patch.object(operator_tools, "MAX_ROWS", 1):
            result = self._find()

        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["results"]), 1)
        self.assertTrue(result["truncated"])

    def test_a_tool_that_raises_becomes_an_error_result_not_a_dead_turn(self):
        def explode(request, payload):
            raise RuntimeError("boom")

        with patch.dict(operator_tools.RUNNERS, {"find_zones": explode}):
            result = operator_tools.run(_request(self.farmer), "find_zones", {"facility": ""})

        self.assertEqual(result["error"], "failed")


class AuditTests(OperatorFixture):
    def test_a_passport_read_through_a_question_is_written_to_the_audit_log(self):
        """"Who looked at my lot" is a question this platform promises to answer."""
        operator_tools.run(
            _request(self.farmer), "lot_passport", {"code": self.mine.code}
        )

        entry = AuditEntry.objects.get()
        self.assertEqual(entry.actor_user, self.farmer)
        self.assertEqual(entry.actor_party, self.zarafshon)
        self.assertEqual(entry.action_key, "a_viewed")
        self.assertEqual(entry.object_ref, self.mine.code)
        self.assertEqual(entry.capability, "view")
        # What lets an auditor tell a read made through a screen from one made
        # through a question.
        self.assertTrue(entry.context["assistant"])

    def test_a_survey_of_lots_is_not_audited_as_a_passport_read(self):
        # Otherwise one question about "my lots" writes a hundred lines saying
        # their owner looked at each of them, and the log stops being readable.
        operator_tools.run(
            _request(self.farmer),
            "find_lots",
            {"status": "", "product": "", "zone": "", "pledged": "", "expiring_within_days": 0},
        )

        self.assertFalse(AuditEntry.objects.exists())

    def test_a_passport_refused_for_scope_writes_nothing(self):
        operator_tools.run(
            _request(self.farmer), "lot_passport", {"code": self.theirs.code}
        )

        self.assertFalse(AuditEntry.objects.exists())


class OperatorBriefTests(OperatorFixture):
    def test_the_brief_tells_the_model_who_it_is_talking_to(self):
        text = brief.who_is_asking(self.officer, panel="/bank/liens")

        self.assertIn("Agrobank ATB", text)
        self.assertIn("credit_officer", text)
        self.assertIn("view", text)
        self.assertIn("/bank/liens", text)
        self.assertIn("not a platform role", text)

    def test_a_platform_role_is_named_as_one(self):
        self.assertIn("platform role", brief.who_is_asking(self.auditor))

    def test_the_two_halves_are_cached_separately(self):
        blocks = brief.operator_system_blocks(self.farmer, "/farmer/lots")

        # The first block is identical for every operator in the deployment;
        # the second is this person's own standing. Different lifetimes, so
        # they get a breakpoint each.
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(blocks[1]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(
            blocks[0]["text"],
            brief.operator_system_blocks(self.officer, "/bank/liens")[0]["text"],
        )
        self.assertIn("Zarafshon", blocks[1]["text"])

    def test_both_assistants_are_briefed_on_the_same_rules(self):
        """One copy of them, because two would drift."""
        self.assertIn(brief.RULES, brief.PROGRAMME)
        self.assertIn(brief.RULES, brief.OPERATOR)


@override_settings(ASSISTANT_ADAPTER="auto", ASSISTANT_API_KEY="")
class PanelEndpointTests(OperatorFixture):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def test_the_panel_endpoint_refuses_anonymous_callers(self):
        response = self.client.post(
            reverse("assistant:panel-ask"),
            data={"question": "How many lots do I have?"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)

    def test_a_signed_in_operator_gets_the_stream(self):
        self.client.force_login(self.farmer)

        response = self.client.post(
            reverse("assistant:panel-ask"),
            data={"question": "How many lots do I have?", "lang": "uz", "panel": "/farmer/lots"},
            content_type="application/json",
        )
        body = b"".join(response.streaming_content).decode()

        self.assertEqual(response["Content-Type"], "text/event-stream")
        self.assertIn('"code": "unavailable"', body)

    def test_somebody_whose_role_carries_no_capabilities_is_refused(self):
        stranger = User.objects.create(username="nobody")
        self.client.force_login(stranger)

        response = self.client.post(
            reverse("assistant:panel-ask"),
            data={"question": "Show me everything."},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)


@override_settings(ASSISTANT_ADAPTER="auto", ASSISTANT_API_KEY="sk-ant-test")
class OperatorTurnTests(OperatorFixture):
    """The loop, driven against a fake stream: the scoped tools really run."""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, client, "_fallbacks_available", True)

    def test_a_survey_runs_the_scoped_tool_and_feeds_the_rows_back(self):
        from apps.assistant.tests.test_assistant import FakeStream, _message

        call = SimpleNamespace(
            type="tool_use",
            id="toolu_1",
            name="find_lots",
            input={
                "status": "",
                "product": "",
                "zone": "",
                "pledged": "yes",
                "expiring_within_days": 0,
            },
        )
        streams = [
            FakeStream("", _message("tool_use", [call])),
            FakeStream("One lot is pledged.", _message("end_turn")),
        ]
        sent: list[dict] = []

        def fake_open(*, fallbacks, **params):
            sent.append(params)
            return streams.pop(0)

        with patch("apps.assistant.client._open", fake_open):
            events = list(
                client.answer_for_operator(
                    _request(self.farmer),
                    question="Which of my lots are pledged?",
                    lang="en",
                    panel="/farmer/lots",
                )
            )

        # The brief carried who is asking, and the tools were the scoped set.
        self.assertIn("Zarafshon", sent[0]["system"][1]["text"])
        # The whole surface, in a fixed order - it is part of the cached
        # prefix, so a reordering is a cache miss for every operator.
        self.assertEqual(
            [tool["name"] for tool in sent[0]["tools"]],
            [
                "find_lots",
                "lot_passport",
                "find_zones",
                "find_liens",
                "find_arrivals",
                "find_excursions",
                "find_qc",
                "find_trials",
                "find_applications",
                "find_policies",
                "find_claims",
                "find_shipments",
                "find_exports",
                "find_organisations",
            ],
        )
        # Only the one that genuinely needs a field is strict; the rest have
        # optional filters, which is what stops the model inventing values for
        # filters it does not want.
        strict = [t["name"] for t in sent[0]["tools"] if t.get("strict")]
        self.assertEqual(strict, ["lot_passport"])
        self.assertEqual(sent[0]["output_config"], {"effort": "medium"})

        # The result fed back was the caller's own lot, and only it.
        result = json.loads(sent[1]["messages"][-1]["content"][0]["content"])
        self.assertEqual([row["code"] for row in result["results"]], [self.mine.code])
        self.assertEqual(events[-1][0], "done")


class ClusterScopeTests(OperatorFixture):
    """The ten tools added after the assistant answered from the wrong table.

    Each one reaches into a cluster the first four could not, so each one is a
    new way to leak. The fixture has two farms, a bank and an insurer, and
    every test below asks the same question of a different cluster: does a
    party see its own row and not its neighbour's.
    """

    def setUp(self):
        super().setUp()
        from datetime import date

        from apps.commercial.models import ExportContract, Shipment, ShipmentLine
        from apps.finance.models import Claim, Policy
        from apps.quality.models import QcRecord
        from apps.registry.models import Farm, OrganisationType, Party
        from apps.storage.models import ConditionExcursion, GateArrival

        carrier_type = OrganisationType.objects.create(code="carrier", label_key="ot_car")
        self.carrier = Party.objects.create(
            code="P-CAR-1", legal_name="Sovuq Yo‘l MChJ", type=carrier_type
        )
        insurer_type = OrganisationType.objects.create(code="insurer", label_key="ot_ins")
        self.insurer = Party.objects.create(
            code="P-INS-1", legal_name="Uzagrosug‘urta AJ", type=insurer_type
        )

        self.mine_farm = Farm.objects.create(
            code="F-MINE", party=self.zarafshon, name="Zarafshon dala"
        )
        self.their_farm = Farm.objects.create(
            code="F-THEIRS", party=self.rival, name="Xorazm dala"
        )
        GateArrival.objects.create(
            facility=self.facility, farm=self.mine_farm, product=self.product,
            expected_at=timezone.now(), status=GateArrival.Status.QUEUED,
        )
        GateArrival.objects.create(
            facility=self.other_facility, farm=self.their_farm, product=self.product,
            expected_at=timezone.now(), status=GateArrival.Status.QUEUED,
        )

        ConditionExcursion.objects.create(
            code="EXC-MINE", scope_type="storage_zone", scope_code="Z-ZEROCO-01",
            started_at=timezone.now(), peak_value=8, threshold=4, severity="critical",
            affected_lot_codes=[self.mine.code],
        )
        ConditionExcursion.objects.create(
            code="EXC-THEIRS", scope_type="storage_zone", scope_code="Z-XRZ-01",
            started_at=timezone.now(), peak_value=9, threshold=4, severity="critical",
            affected_lot_codes=[self.theirs.code],
        )

        QcRecord.objects.create(
            lot=self.mine, stage="intake", inspected_on=date(2026, 8, 14), defect_pct=1
        )
        QcRecord.objects.create(
            lot=self.theirs, stage="intake", inspected_on=date(2026, 8, 14), defect_pct=2
        )

        self.my_policy = Policy.objects.create(
            code="POL-MINE", insurer_party=self.insurer, holder_party=self.zarafshon,
            starts_on=date(2026, 1, 1), ends_on=date(2027, 1, 1),
            amount_minor=90_000_000, currency="UZS",
        )
        their_policy = Policy.objects.create(
            code="POL-THEIRS", insurer_party=self.insurer, holder_party=self.rival,
            starts_on=date(2026, 1, 1), ends_on=date(2027, 1, 1),
            amount_minor=50_000_000, currency="UZS",
        )
        Claim.objects.create(
            code="CLM-MINE", policy=self.my_policy, lot=self.mine,
            amount_minor=10_000_000, currency="UZS",
        )
        Claim.objects.create(
            code="CLM-THEIRS", policy=their_policy, lot=self.theirs,
            amount_minor=20_000_000, currency="UZS",
        )

        self.my_export = ExportContract.objects.create(
            code="EX-MINE", seller_party=self.zarafshon, buyer_name="Tokyo Fruits",
            buyer_country="JP", product=self.product, amount_minor=1, currency="USD",
        )
        their_export = ExportContract.objects.create(
            code="EX-THEIRS", seller_party=self.rival, buyer_name="Seoul Fresh",
            buyer_country="KR", product=self.product, amount_minor=1, currency="USD",
        )
        self.my_shipment = Shipment.objects.create(
            code="SH-MINE", export_contract=self.my_export, carrier_party=self.carrier,
            origin="Samarqand", destination="Tokyo", status=Shipment.Status.IN_TRANSIT,
        )
        ShipmentLine.objects.create(
            shipment=self.my_shipment, lot=self.mine, quantity_g=1_000_000
        )
        Shipment.objects.create(
            code="SH-THEIRS", export_contract=their_export, carrier_party=self.carrier,
            origin="Xorazm", destination="Seoul", status=Shipment.Status.PLANNED,
        )

        self.underwriter = self._person("ins", self.insurer, self.bank_role)
        self.hauler = self._person("car", self.carrier, self.bank_role)

    def codes(self, user, tool, payload=None, key="code"):
        result = operator_tools.run(_request(user), tool, payload or {})
        self.assertNotIn("error", result, msg=f"{tool} refused: {result.get('detail')}")
        return sorted(row[key] for row in result["results"])

    def test_the_gate_queue_is_the_yard_you_run_or_the_produce_you_sent(self):
        self.assertEqual(self.codes(self.farmer, "find_arrivals", key="farm"), ["F-MINE"])

    def test_an_excursion_reaches_you_through_your_zone_or_your_lot(self):
        # The producer operates the facility here, so both routes point at the
        # same row; what matters is that the neighbour's does not appear.
        self.assertEqual(self.codes(self.farmer, "find_excursions"), ["EXC-MINE"])
        self.assertEqual(
            self.codes(self.auditor, "find_excursions"), ["EXC-MINE", "EXC-THEIRS"]
        )

    def test_quality_records_follow_the_lots_you_can_see(self):
        self.assertEqual(self.codes(self.farmer, "find_qc", key="lot"), [self.mine.code])

    def test_policies_and_claims_reach_the_holder_and_the_underwriter(self):
        self.assertEqual(self.codes(self.farmer, "find_policies"), ["POL-MINE"])
        self.assertEqual(
            self.codes(self.underwriter, "find_policies"), ["POL-MINE", "POL-THEIRS"]
        )
        self.assertEqual(self.codes(self.farmer, "find_claims"), ["CLM-MINE"])

    def test_a_shipment_reaches_you_as_carrier_seller_or_owner_of_the_cargo(self):
        # The carrier drives both; the producer reaches one, through the lot on
        # board and through having sold it.
        self.assertEqual(
            self.codes(self.hauler, "find_shipments"), ["SH-MINE", "SH-THEIRS"]
        )
        self.assertEqual(self.codes(self.farmer, "find_shipments"), ["SH-MINE"])
        self.assertEqual(self.codes(self.farmer, "find_exports"), ["EX-MINE"])

    def test_the_register_is_a_refusal_for_anyone_but_platform_staff(self):
        refused = operator_tools.run(_request(self.farmer), "find_organisations", {})

        # A refusal, not an empty list: an empty register would read as "there
        # are no organisations", which is a different and false statement.
        self.assertEqual(refused["error"], "forbidden")
        self.assertGreater(
            operator_tools.run(_request(self.auditor), "find_organisations", {})["total"],
            0,
        )

    def test_trials_are_open_to_everyone_because_the_website_publishes_them(self):
        from apps.quality.models import PilotTrial

        PilotTrial.objects.create(
            code="TR-MELON-01", product=self.product, schedule_days=[0, 7, 14]
        )
        # Scoping this would leave the operator running the trial seeing less of
        # it than an anonymous visitor to the website does.
        for who in (self.farmer, self.officer, self.auditor):
            self.assertEqual(self.codes(who, "find_trials"), ["TR-MELON-01"])

    def test_a_bad_filter_is_an_error_in_every_cluster_not_only_in_lots(self):
        for tool, payload in [
            ("find_applications", {"status": "pondering"}),
            ("find_claims", {"status": "pondering"}),
            ("find_shipments", {"status": "teleporting"}),
            ("find_exports", {"status": "pondering"}),
            ("find_arrivals", {"open_only": "perhaps"}),
        ]:
            with self.subTest(tool=tool):
                self.assertEqual(
                    operator_tools.run(_request(self.auditor), tool, payload)["error"],
                    "bad_filter",
                )

    def test_awaiting_a_decision_does_not_mean_merely_unverified(self):
        """A rejected organisation has been decided, so it is not waiting.

        The first version excluded everything that was not verified, which
        swept rejections into the "still awaiting verification" count - the one
        number anybody asks this filter for.
        """
        from apps.registry.models import Party

        self.rival.verification_status = Party.Verification.REJECTED
        self.rival.save(update_fields=["verification_status"])
        self.zarafshon.verification_status = Party.Verification.REVIEW
        self.zarafshon.save(update_fields=["verification_status"])

        waiting = self.codes(self.auditor, "find_organisations", {"pending_only": "yes"})

        self.assertIn(self.zarafshon.code, waiting)
        self.assertNotIn(self.rival.code, waiting)


class RenderedQuantityTests(OperatorFixture):
    """Money and weight arrive as strings, because arithmetic went wrong once.

    Asked in Russian what an insurer covered, the assistant reported a lot
    valued at 168,000,000 UZS as 16,800,000 - a tenfold understatement, beside
    three other figures it had divided correctly. It had been handed
    `valuation_minor: 16800000000` and asked to do the sum in its head. Now it
    is handed the sentence.
    """

    def test_money_arrives_already_converted_with_its_currency(self):
        result = operator_tools.run(
            _request(self.farmer),
            "find_lots",
            {},
        )
        row = result["results"][0]

        # The raw field stays - it is the truth, and rule 5 says so.
        self.assertEqual(row["valuation_minor"], 120_000_000)
        # And beside it, the thing to copy into a sentence.
        self.assertEqual(row["valuation"], "1,200,000.00 UZS")

    def test_weight_arrives_in_kilograms(self):
        row = operator_tools.run(_request(self.farmer), "find_lots", {})["results"][0]

        self.assertEqual(row["net_weight_g"], 4_200_000)
        # No trailing decimal on a whole number of kilograms - the model copies
        # these verbatim, and "4,200.0 kg" mid-sentence reads like an
        # instrument reading rather than a weight.
        self.assertEqual(row["net_weight"], "4,200 kg")

    def test_a_part_kilogram_keeps_its_decimal(self):
        self.assertEqual(
            operator_tools._rendered({"net_weight_g": 4_250_500})["net_weight"],
            "4,250.5 kg",
        )

    def test_a_lien_amount_takes_the_currency_beside_it(self):
        lien = operator_tools.run(_request(self.farmer), "find_liens", {})["results"][0]

        self.assertEqual(lien["amount"], "500,000.00 UZS")

    def test_money_with_no_currency_to_be_found_is_left_alone(self):
        """Never guessed. This project spans UZS, USD and JPY.

        A number carrying the wrong currency is worse than one the reader has
        to go and look up.
        """
        rendered = operator_tools._rendered({"amount_minor": 5000})

        self.assertNotIn("amount", rendered)
        self.assertEqual(rendered["amount_minor"], 5000)

    def test_rendering_reaches_into_nested_rows(self):
        # A passport carries money and weight several levels down.
        passport = operator_tools.run(
            _request(self.farmer), "lot_passport", {"code": self.mine.code}
        )

        self.assertEqual(passport["lot"]["net_weight"], "4,200 kg")

    def test_a_flag_is_not_mistaken_for_a_quantity(self):
        # `True` is an int in Python, and a booking flag rendered as "0.0 kg"
        # would be a new way to be wrong.
        rendered = operator_tools._rendered({"passed_g": True})

        self.assertNotIn("passed", rendered)


@override_settings(ASSISTANT_ADAPTER="auto", ASSISTANT_API_KEY="")
class AcceptHeaderTests(OperatorFixture):
    """Both endpoints must answer the header the widgets actually send.

    DRF negotiates content **before** the view body runs. With only
    `JSONRenderer` declared, a client asking for `Accept: text/event-stream` -
    which both widgets do, because that is what they are about to read - got
    406 and never reached the streaming code at all. The assistant did not work
    in a browser, and could not have.

    Every end-to-end check missed it because `curl` sends `Accept: */*` unless
    told otherwise, and `*/*` is satisfied by JSON. These ask the way the
    browser asks.
    """

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def _ask(self, path, **extra):
        return self.client.post(
            path,
            data={"question": "How many lots do I have?"},
            content_type="application/json",
            HTTP_ACCEPT="text/event-stream",
            **extra,
        )

    def test_the_panel_endpoint_accepts_the_event_stream_header(self):
        self.client.force_login(self.farmer)

        response = self._ask(reverse("assistant:panel-ask"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")
        self.assertIn(b"event:", b"".join(response.streaming_content))

    def test_the_public_endpoint_accepts_it_too(self):
        response = self._ask(reverse("assistant:ask"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")

    def test_a_client_asking_for_json_is_still_served(self):
        # `*/*` is what curl and the schema tooling send; it must keep working.
        self.client.force_login(self.farmer)

        response = self.client.post(
            reverse("assistant:panel-ask"),
            data={"question": "How many lots do I have?"},
            content_type="application/json",
            HTTP_ACCEPT="*/*",
        )

        self.assertEqual(response.status_code, 200)

    def test_a_refusal_reaches_an_event_stream_client_as_a_readable_frame(self):
        """A throttle or a permission is raised before the view streams anything.

        Rendered through the event-stream renderer it becomes one `error`
        frame in the shape the client's parser already knows, rather than a
        stream that opens and closes saying nothing.
        """
        stranger = User.objects.create(username="nocaps")
        self.client.force_login(stranger)

        response = self._ask(reverse("assistant:panel-ask"))

        self.assertEqual(response.status_code, 403)
        self.assertIn(b"event: error", response.content)
        self.assertIn(b"signed_out", response.content)
