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

        facility = Facility.objects.create(
            code="F-SMQ-1", name="Samarqand hub", operator_party=self.zarafshon
        )
        Facility.objects.create(
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
            facility=Facility.objects.get(code="F-XRZ-1"),
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

    def test_an_unknown_status_returns_nothing_rather_than_everything(self):
        self.assertEqual(self._find(status="teleported")["total"], 0)

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
        self.assertEqual(
            [tool["name"] for tool in sent[0]["tools"]],
            ["find_lots", "lot_passport", "find_zones", "find_liens"],
        )
        self.assertEqual(sent[0]["output_config"], {"effort": "medium"})

        # The result fed back was the caller's own lot, and only it.
        result = json.loads(sent[1]["messages"][-1]["content"][0]["content"])
        self.assertEqual([row["code"] for row in result["results"]], [self.mine.code])
        self.assertEqual(events[-1][0], "done")
