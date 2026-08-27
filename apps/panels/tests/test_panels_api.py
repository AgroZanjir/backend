"""The panel API, against the seeded dataset.

These run the real seed commands rather than hand-built fixtures. That is
deliberate: the seed is what a demonstration, a pilot and every developer's
database are built from, so a change that breaks it should break the suite.
"""

from django.core.management import call_command
from django.test import TestCase

from apps.lots.models import Lot


class PanelApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_reference", verbosity=0)
        call_command("seed_demo", verbosity=0)

    def sign_in(self, persona: str) -> dict:
        response = self.client.post(
            "/api/v1/auth/oneid/", {"persona": persona}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200, response.content[:200])
        return {"authorization": f"Bearer {response.json()['access']}"}

    def get(self, url: str, persona: str = "d.yusupov"):
        return self.client.get(url, headers=self.sign_in(persona))

    # -- reads -----------------------------------------------------------

    def test_reference_carries_the_whole_role_model(self):
        body = self.get("/api/v1/panels/reference/").json()

        self.assertEqual(len(body["products"]), 6)
        self.assertEqual(len(body["zones"]), 6)
        self.assertEqual(len(body["org_types"]), 13)
        self.assertEqual(len(body["capabilities"]), 10)
        self.assertEqual(sum(len(g["roles"]) for g in body["role_groups"]), 37)

    def test_a_hub_operator_sees_the_hub_s_lots(self):
        rows = self.get("/api/v1/panels/lots/").json()["results"]
        self.assertTrue(rows)
        self.assertIn("pledged", rows[0])

    def test_the_bank_sees_the_lots_pledged_to_it_and_not_the_rest(self):
        rows = self.get("/api/v1/panels/lots/", "a.bekmurodov").json()["results"]
        codes = {row["code"] for row in rows}

        self.assertIn("AZ-2026-SMQ-0412", codes)  # pledged to Agrobank
        self.assertNotIn("AZ-2026-SMQ-0421", codes)  # nobody's collateral

    def test_the_passport_carries_the_chain_and_says_it_is_intact(self):
        body = self.get("/api/v1/panels/lots/AZ-2026-SMQ-0412/").json()

        self.assertTrue(body["chain_intact"])
        self.assertEqual(len(body["events"]), 8)
        self.assertEqual(len(body["qc"]), 3)
        self.assertTrue(body["lot"]["pledged"])
        self.assertEqual(body["zone"]["code"], "Z-ZEROCO-01")

    def test_the_public_passport_is_open_but_carries_no_commerce(self):
        response = self.client.get("/api/v1/panels/public/lots/AZ-2026-SMQ-0412/")

        self.assertEqual(response.status_code, 200)
        lot = response.json()["lot"]
        for private in ("valuation_minor", "pledged", "owner_name"):
            self.assertNotIn(private, lot)
        # What it does prove: provenance and handling.
        body = response.json()
        self.assertEqual(lot["product"], "melon")
        self.assertEqual(body["origin"]["region"], "Samarqand")
        self.assertEqual(body["origin"]["certifications"], ["GlobalGAP"])
        self.assertTrue(body["events"])
        # ...and not what the farmer's bank and insurer are doing about it.
        self.assertNotIn("pledged", {e["type"] for e in body["events"]})
        self.assertNotIn("excursion", {e["type"] for e in body["events"]})
        self.assertNotIn("measurements", body["qc"][0])

    def test_the_trial_keeps_measurements_and_projections_apart(self):
        body = self.get("/api/v1/panels/trials/TR-MELON-01/").json()
        zeroco = body["arms"]["zeroco"]

        self.assertEqual(len(body["schedule_days"]), 9)
        self.assertEqual(len(zeroco["observations"]), 2)  # only what was measured
        self.assertEqual(len(zeroco["projection"]["loss"]), 9)  # the modelled curve
        self.assertEqual(body["observed_points"], 2)

    def test_zone_fill_comes_from_open_placements(self):
        zones = {z["code"]: z for z in self.get("/api/v1/panels/zones/").json()["results"]}

        # The two melon lots and the grape lot are in ZEROCO-01.
        self.assertEqual(zones["Z-ZEROCO-01"]["used_g"], (4200 + 11400) * 1000)
        self.assertEqual(zones["Z-COLD-02"]["off_band"], True)

    # -- permissions -----------------------------------------------------

    def test_the_administration_panel_is_closed_to_a_qc_inspector(self):
        self.assertEqual(self.get("/api/v1/panels/admin/organisations/").status_code, 403)

    def test_the_administration_panel_opens_for_a_platform_role(self):
        response = self.get("/api/v1/panels/admin/organisations/", "m.tulyaganova")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["results"]), 10)

    def test_an_organisation_under_review_shows_which_checks_apply(self):
        body = self.get(
            "/api/v1/panels/admin/organisations/ORG-00511/", "m.tulyaganova"
        ).json()

        self.assertEqual(body["status"], "review")
        self.assertEqual(set(body["checks"]), set(body["required_checks"]))
        # A farmer has no sector licence to verify, so it is not even recorded.
        self.assertNotIn("licence", body["checks"])

    def test_reads_are_audited(self):
        self.get("/api/v1/panels/lots/AZ-2026-SMQ-0412/")
        entries = self.get("/api/v1/panels/admin/audit/", "m.tulyaganova").json()["results"]

        viewed = [e for e in entries if e["object_ref"] == "AZ-2026-SMQ-0412"]
        self.assertTrue(viewed)
        self.assertEqual(viewed[0]["capability"], "view")

    # -- writes ----------------------------------------------------------

    def test_registering_a_lot_at_the_gate_starts_its_log(self):
        response = self.client.post(
            "/api/v1/lots/",
            {
                "product": "melon",
                "farm": "F-SMQ-014",
                "gross_weight_g": 4_310_000,
                "net_weight_g": 4_200_000,
                "harvested_on": "2026-08-26",
                "facility": "GATE-01",
                "idempotency_key": "test-1",
            },
            content_type="application/json",
            headers=self.sign_in("g.rasulova"),
        )

        self.assertEqual(response.status_code, 201, response.content[:300])
        lot = Lot.objects.get(code=response.json()["code"])
        self.assertEqual(lot.events.count(), 1)
        self.assertTrue(lot.chain_intact)

    def test_the_gate_is_idempotent_because_the_field_loses_connectivity(self):
        payload = {
            "product": "melon",
            "farm": "F-SMQ-014",
            "net_weight_g": 4_200_000,
            "idempotency_key": "same-key",
        }
        headers = self.sign_in("g.rasulova")
        first = self.client.post(
            "/api/v1/lots/", payload, content_type="application/json", headers=headers
        )
        second = self.client.post(
            "/api/v1/lots/", payload, content_type="application/json", headers=headers
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["code"], second.json()["code"])

    def test_dispatch_is_refused_while_a_lien_is_live_and_names_it(self):
        response = self.client.post(
            "/api/v1/lots/AZ-2026-SMQ-0412/dispatch/",
            content_type="application/json",
            headers=self.sign_in("d.yusupov"),
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("Agrobank ATB", response.json()["blockers"][0])

    def test_a_role_without_the_capability_is_refused(self):
        """A credit officer may transact; they may not put stock away."""
        response = self.client.post(
            "/api/v1/storage/placements/",
            {"lot": "AZ-2026-SMQ-0421", "zone": "Z-COLD-01"},
            content_type="application/json",
            headers=self.sign_in("a.bekmurodov"),
        )

        self.assertEqual(response.status_code, 403)

    def test_an_ungraded_lot_cannot_be_put_away(self):
        response = self.client.post(
            "/api/v1/storage/placements/",
            {"lot": "AZ-2026-SMQ-0421", "zone": "Z-ZEROCO-02"},
            content_type="application/json",
            headers=self.sign_in("s.ergashev"),
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("Grade", response.json()["detail"])

    def test_placing_a_lot_moves_it_and_sets_the_saleable_window(self):
        self.client.post(
            "/api/v1/lots/AZ-2026-SMQ-0421/grade/",
            {"grade": "A"},
            content_type="application/json",
            headers=self.sign_in("d.yusupov"),
        )
        response = self.client.post(
            "/api/v1/storage/placements/",
            {"lot": "AZ-2026-SMQ-0421", "zone": "Z-ZEROCO-02", "position": "A-01"},
            content_type="application/json",
            headers=self.sign_in("s.ergashev"),
        )

        self.assertEqual(response.status_code, 201, response.content[:300])
        lot = Lot.objects.get(code="AZ-2026-SMQ-0421")
        self.assertEqual(lot.status, "stored")
        self.assertEqual(lot.storage_mode, "zeroco")
        # Pomegranate keeps 90 days in a ZEROCO chamber, from harvest.
        self.assertEqual(str(lot.sell_by), "2026-11-23")

    def test_a_zone_will_not_take_more_than_it_holds(self):
        self.client.post(
            "/api/v1/lots/AZ-2026-SMQ-0421/grade/",
            {"grade": "A"},
            content_type="application/json",
            headers=self.sign_in("d.yusupov"),
        )
        # ZEROCO-01 holds 20 t and 15.6 t of it is spoken for; this lot is 5.6 t.
        response = self.client.post(
            "/api/v1/storage/placements/",
            {"lot": "AZ-2026-SMQ-0421", "zone": "Z-ZEROCO-01"},
            content_type="application/json",
            headers=self.sign_in("s.ergashev"),
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("room", response.json()["detail"])

    # -- what each screen opens on ---------------------------------------

    def test_the_zone_list_puts_the_zeroco_chambers_first(self):
        """The scarce capacity is the room a hub manager looks for first."""
        codes = [z["code"] for z in self.get("/api/v1/panels/zones/").json()["results"]]

        self.assertEqual(codes[:2], ["Z-ZEROCO-01", "Z-ZEROCO-02"])

    def test_the_trial_list_puts_the_running_trials_first(self):
        rows = self.get("/api/v1/panels/trials/").json()["results"]

        self.assertEqual(rows[0]["status"], "running")
        # A planned trial has no arms yet; opening the comparison screen on one
        # drew an empty chart.
        self.assertGreaterEqual(rows[0]["arms"], 1)

    def test_a_claim_names_the_excursion_its_evidence_comes_from(self):
        """The insurer's evidence screen and the claim must be about one incident."""
        claims = self.get("/api/v1/panels/claims/", "m.karimova").json()["results"]
        open_claim = next(c for c in claims if c["status"] == "review")

        self.assertEqual(open_claim["excursion"], "EXC-2026-0311")
        excursion = self.get(
            f"/api/v1/panels/excursions/{open_claim['excursion']}/", "m.karimova"
        ).json()
        self.assertEqual(excursion["severity"], "critical")
        self.assertIn(open_claim["lot"], excursion["lots"])
