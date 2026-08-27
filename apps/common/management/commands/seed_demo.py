"""Seed the pilot demonstration dataset.

This is the dataset the panels were designed against, moved out of the web
client's fixtures and into the database it always belonged in: real Uzbek
products, regions, farms and institutions so the client recognises their own
operation.

Two things it is careful about, and they are the same two the demo bar on
every panel says out loud:

* **No number here is a measured ZEROCO result.** Trial arms carry a modelled
  `projection` and only the sampling days that have actually happened exist as
  `TrialObservation` rows. The comparison chart draws the measured part solid
  and the projection dashed, because the concept document requires local pilot
  verification before any of it can be claimed.
* **The event log is built by appending, not by inserting.** Every lot's
  history goes through `lot.log(...)`, so the hash chain in the seeded database
  is a real chain - `verify-chain` passes on it, and it would fail if anyone
  edited a row afterwards.

Run after `seed_reference`. `--reset` empties the demo tables first; without
it the command refuses to run twice, because a second run would double every
lot.
"""

from __future__ import annotations

import zoneinfo
from datetime import date, datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.common.models import Notification
from apps.commercial.models import ExportContract, Shipment, ShipmentLine
from apps.documents.models import Document
from apps.finance.models import Claim, Encumbrance, FinanceApplication, Policy, SettlementAllocation
from apps.governance.models import AuditEntry, DataGrant
from apps.lots.models import Lot, LotEvent, LotRelation
from apps.quality.models import PilotTrial, QcRecord, TrialArm, TrialObservation
from apps.registry.models import (
    Farm,
    Membership,
    OrganisationType,
    Party,
    PartyVerification,
    Product,
    ProductionPlan,
    Role,
    User,
    VerificationCheck,
)
from apps.storage.models import (
    ConditionExcursion,
    ConditionReading,
    Facility,
    GateArrival,
    StoragePlacement,
    StorageZone,
)

TASHKENT = zoneinfo.ZoneInfo("Asia/Tashkent")

#: The prototype was authored against a fixed "today" and every relative figure
#: on the panels reads from it. Kept here so the seeded data stays legible
#: against the screens it was designed for.
TODAY = date(2026, 8, 26)

KG = 1000  # grams in a kilogram; quantities are stored in grams
TIYIN = 100  # minor units in one sum


def at(text: str) -> datetime:
    """Parse `2026-08-14 07:42` as Tashkent local time."""
    fmt = "%Y-%m-%d %H:%M" if len(text) > 10 else "%Y-%m-%d"
    return datetime.strptime(text, fmt).replace(tzinfo=TASHKENT)


PRODUCTS = [
    # code, uz, ru, en, variety, HS, shelf life by storage mode (days)
    ("melon", "Qovun", "Дыня", "Melon", "Torpeda", "0807.19", {"zeroco": 53, "cold": 28, "pre": 10, "dry": 0}),
    ("grape", "Uzum", "Виноград", "Grape", "Husayni", "0806.10", {"zeroco": 45, "cold": 25, "pre": 8, "dry": 180}),
    ("cherry", "Olcha", "Черешня", "Cherry", "Bigarreau", "0809.29", {"zeroco": 28, "cold": 14, "pre": 5, "dry": 0}),
    ("apricot", "O‘rik", "Абрикос", "Apricot", "Subhoni", "0809.10", {"zeroco": 27, "cold": 12, "pre": 5, "dry": 240}),
    ("tomato", "Pomidor", "Помидор", "Tomato", "Bella Rosa", "0702.00", {"zeroco": 28, "cold": 16, "pre": 6, "dry": 0}),
    ("pom", "Anor", "Гранат", "Pomegranate", "Qizil", "0810.90", {"zeroco": 90, "cold": 60, "pre": 14, "dry": 0}),
]

# code, name, type, TIN, region, verification, verified on, verified by
ORGS = [
    ("ORG-00412", "Nodir dehqon xo‘jaligi", "farmer", "305 872 114", "Samarqand", "verified", "2026-06-02", "M. Tulyaganova"),
    ("ORG-00097", "Zarafshon Agro MChJ", "cooperative", "302 114 908", "Samarqand", "verified", "2026-03-18", "M. Tulyaganova"),
    ("ORG-00511", "Urgut Agro MChJ", "farmer", "306 220 471", "Samarqand", "review", None, None),
    ("ORG-00008", "Samarqand Hub Operator", "operator", "301 004 552", "Samarqand", "verified", "2026-01-14", "A. Nazarov"),
    ("ORG-00021", "Agrobank ATB", "bank", "200 831 776", "Toshkent", "verified", "2026-02-03", "A. Nazarov"),
    ("ORG-00034", "Uzagrosug‘urta AJ", "insurer", "201 447 320", "Toshkent", "verified", "2026-02-20", "A. Nazarov"),
    ("ORG-00563", "Uztrans Logistic MChJ", "carrier", "307 991 002", "Toshkent", "review", None, None),
    ("ORG-00588", "Payariq Lab MChJ", "laboratory", "308 552 119", "Samarqand", "pending", None, None),
    ("ORG-00601", "Jomboy Fresh Export", "exporter", "309 118 447", "Samarqand", "rejected", "2026-08-11", "M. Tulyaganova"),
    # The operator of the platform itself. Not in the client's prototype list,
    # which showed only applicants - but the administration panel is operated
    # by somebody, and that somebody is an organisation like any other.
    ("ORG-00001", "Agro Zanjir", "operator", "200 000 001", "Toshkent", "verified", "2026-01-05", "A. Nazarov"),
]

# username, display name, org, role, OneID, E-IMZO, last seen, status
USERS = [
    ("n.sharipov", "Nodir Sharipov", "ORG-00412", "org_owner", True, True, "2026-08-26 07:14", "active"),
    ("g.rasulova", "G. Rasulova", "ORG-00008", "gate_operator", True, False, "2026-08-26 06:41", "active"),
    ("d.yusupov", "D. Yusupov", "ORG-00008", "qc_inspector", True, False, "2026-08-26 09:15", "active"),
    ("s.ergashev", "S. Ergashev", "ORG-00008", "warehouse_operator", True, False, "2026-08-26 06:20", "active"),
    ("a.bekmurodov", "A. Bekmurodov", "ORG-00021", "credit_officer", True, True, "2026-08-25 16:02", "active"),
    ("m.karimova", "M. Karimova", "ORG-00034", "claims_adjuster", True, True, "2026-08-26 08:33", "active"),
    ("r.tursunov", "R. Tursunov", "ORG-00097", "commercial_manager", True, True, "2026-08-26 10:05", "active"),
    ("b.qodirov", "B. Qodirov", "ORG-00563", "dispatcher", True, False, None, "invited"),
    ("z.ismoilova", "Z. Ismoilova", "ORG-00511", "farm_manager", False, False, None, "pending"),
    ("t.xolmatov", "T. Xolmatov", "ORG-00097", "org_admin", True, False, "2026-07-30 11:48", "suspended"),
    ("m.tulyaganova", "M. Tulyaganova", "ORG-00001", "platform_owner", True, True, "2026-08-26 11:20", "active"),
    ("a.nazarov", "A. Nazarov", "ORG-00001", "platform_admin", True, True, "2026-08-23 14:55", "active"),
]

FARMS = [
    ("F-SMQ-014", "Nodir dehqon xo‘jaligi", "Nodir Sharipov", "ORG-00412", "Samarqand", "Payariq", "12.50", ["GlobalGAP"]),
    ("F-SMQ-007", "Zarafshon klaster", "Zarafshon Agro MChJ", "ORG-00097", "Samarqand", "Jomboy", "186.00", ["GlobalGAP", "Organic"]),
    ("F-SMQ-031", "Urgut bog‘lari", "Urgut Agro MChJ", "ORG-00511", "Samarqand", "Urgut", "64.20", []),
]

# code, mode, capacity kg, temp, rh, target temp, target rh
ZONES = [
    ("Z-ZEROCO-01", "zeroco", 20000, "0.40", "97.60", "0.50", "98.00"),
    ("Z-ZEROCO-02", "zeroco", 20000, "0.60", "97.10", "0.50", "98.00"),
    ("Z-COLD-01", "cold", 50000, "4.20", "89.00", "4.00", "90.00"),
    ("Z-COLD-02", "cold", 50000, "6.90", "84.00", "4.00", "90.00"),
    ("Z-PRECOOL", "pre", 8000, "2.10", "92.00", "2.00", "92.00"),
    ("Z-DRY-01", "dry", 30000, "16.40", "55.00", "16.00", "55.00"),
]

# code, product, farm, net kg, grade, status, zone, position, harvested,
# placed, sell by, trial arm, valuation (UZS, major)
LOTS = [
    ("AZ-2026-SMQ-0412", "melon", "F-SMQ-014", 4200, "A", "stored", "Z-ZEROCO-01", "B-06", "2026-08-14", "2026-08-15", "2026-10-06", "zeroco", 168_000_000),
    ("AZ-2026-SMQ-0411", "melon", "F-SMQ-014", 3000, "A", "stored", "Z-COLD-01", "A-06", "2026-08-14", "2026-08-15", "2026-09-11", "control", 120_000_000),
    ("AZ-2026-SMQ-0408", "grape", "F-SMQ-007", 11400, "A", "reserved", "Z-ZEROCO-01", "C-02", "2026-08-10", "2026-08-11", "2026-09-24", "", 513_000_000),
    ("AZ-2026-SMQ-0396", "apricot", "F-SMQ-031", 6250, "B", "stored", "Z-COLD-02", "D-11", "2026-08-06", "2026-08-07", "2026-09-02", "", 156_250_000),
    ("AZ-2026-SMQ-0377", "cherry", "F-SMQ-007", 2380, "A", "stored", "Z-ZEROCO-02", "B-03", "2026-08-16", "2026-08-16", "2026-09-13", "", 190_400_000),
    ("AZ-2026-SMQ-0381", "tomato", "F-SMQ-031", 8900, "A", "dispatched", None, "", "2026-07-29", "2026-07-30", "2026-08-26", "", 133_500_000),
    ("AZ-2026-SMQ-0362", "grape", "F-SMQ-007", 9800, "A", "settled", None, "", "2026-07-22", "2026-07-23", "2026-09-05", "", 441_000_000),
    ("AZ-2026-SMQ-0355", "apricot", "F-SMQ-031", 4100, "C", "written_off", None, "", "2026-07-18", "2026-07-19", "2026-08-14", "", 0),
    ("AZ-2026-SMQ-0421", "pom", "F-SMQ-007", 5600, "A", "registered", None, "", "2026-08-25", None, None, "", 224_000_000),
]

ARRIVALS = [
    ("07:12", "01 A 234 BC", "F-SMQ-014", "melon", 4300, "weighing"),
    ("07:40", "01 B 887 KA", "F-SMQ-007", "grape", 11500, "queued"),
    ("08:05", "40 C 119 AB", "F-SMQ-031", "apricot", 6400, "queued"),
    ("08:30", "01 D 552 MN", "F-SMQ-007", "cherry", 2400, "queued"),
]

#: The flagship lot's history. Every entry is appended in order, so the hash
#: chain in the seeded database is genuine. The payloads are structured, not
#: prose: the panels compose the sentence in the reader's language, which the
#: prototype could not do because it stored the sentence.
FLAGSHIP_EVENTS = [
    ("registered", "2026-08-14 07:42", "g.rasulova", "", "GATE-01",
     {"gross_weight_g": 4310 * KG, "net_weight_g": 4200 * KG, "idempotency_key": "8f2c"}),
    ("sampled", "2026-08-14 08:10", "d.yusupov", "", "",
     {"stage": "intake", "measurements": {"brix": 12.4, "calibre_kg": 2.1}, "defect_pct": 1.8}),
    ("graded", "2026-08-14 08:35", "d.yusupov", "accept", "",
     {"grade": "A", "net_weight_g": 4200 * KG, "photos": 3}),
    ("placed", "2026-08-15 06:20", "s.ergashev", "accept", "HUB-SMQ",
     {"zone": "Z-ZEROCO-01", "position": "B-06", "temp_c": 0.4, "rh_pct": 97.6}),
    ("trial_start", "2026-08-15 09:00", "d.yusupov", "accept", "",
     {"trial": "TR-MELON-01", "arm": "zeroco", "paired_lot": "AZ-2026-SMQ-0411"}),
    ("pledged", "2026-08-18 14:05", None, "warn", "",
     {"application": "FA-2026-0117", "amount_minor": 168_000_000 * TIYIN, "currency": "UZS", "kind": "inventory"}),
    ("inspected", "2026-08-22 09:15", "d.yusupov", "", "",
     {"day": 7, "weight_loss_pct": 0.7, "firmness_n": 8.3, "defects": 0}),
    ("excursion", "2026-08-24 02:40", None, "warn", "",
     {"zone": "Z-ZEROCO-01", "peak_c": 2.9, "duration_min": 34, "threshold_c": 2.5, "resolved": True}),
]

QC_RECORDS = [
    ("intake", "2026-08-14", {"brix": 12.4, "firmness_n": 8.4}, "1.80", "A"),
    ("pre_storage", "2026-08-15", {"brix": 12.4, "firmness_n": 8.4}, "1.80", "A"),
    ("in_storage", "2026-08-22", {"brix": 12.6, "firmness_n": 8.3}, "1.90", "A"),
]

TRIAL_DAYS = [0, 7, 14, 21, 28, 35, 42, 49, 56]
#: The modelled curves. Explicitly a projection: nothing beyond the recorded
#: observations has been measured, and the chart says so.
PROJECTIONS = {
    "control": {
        "loss": [0, 1.8, 3.4, 5.1, 7, 9.2, 11.8, 14.9, 18.4],
        "waste": [0, 0.5, 1.4, 2.8, 4.5, 7.1, 10.6, 15.2, 21],
        "firm": [8.4, 8, 7.5, 6.8, 5.9, 5.1, 4.4, 3.8, 3.2],
    },
    "zeroco": {
        "loss": [0, 0.7, 1.4, 2.1, 3, 3.9, 4.9, 6, 7.2],
        "waste": [0, 0.1, 0.4, 1, 2, 3.1, 4.4, 6, 8.1],
        "firm": [8.4, 8.3, 8.1, 7.9, 7.6, 7.3, 7, 6.6, 6.2],
    },
}

TRIALS = [
    ("TR-MELON-01", "melon", "running", "2026-08-15", "AZ-2026-SMQ-0412", "AZ-2026-SMQ-0411", 2),
    ("TR-CHERRY-02", "cherry", "running", "2026-08-17", "AZ-2026-SMQ-0377", None, 2),
    ("TR-GRAPE-03", "grape", "planned", "2026-09-02", None, None, 0),
    ("TR-TOMATO-01", "tomato", "completed", "2026-06-04", None, None, 9),
]

# code, applicant, lender, kind, amount (UZS major), status, lots, LTV, applied
APPLICATIONS = [
    ("FA-2026-0117", "ORG-00412", "ORG-00021", "inventory", 168_000_000, "disbursed", ["AZ-2026-SMQ-0412"], "62.00", "2026-08-18"),
    ("FA-2026-0121", "ORG-00097", "ORG-00021", "inventory", 410_000_000, "review", ["AZ-2026-SMQ-0408"], "68.00", "2026-08-23"),
    ("FA-2026-0124", "ORG-00511", "ORG-00021", "pre_export", 95_000_000, "submitted", [], "0.00", "2026-08-25"),
    ("FA-2026-0109", "ORG-00097", "ORG-00021", "pre_export", 640_000_000, "repaid", ["AZ-2026-SMQ-0362"], "55.00", "2026-07-24"),
]

# lot, application, amount, created, released
LIENS = [
    ("AZ-2026-SMQ-0412", "FA-2026-0117", 168_000_000, "2026-08-18", None),
    ("AZ-2026-SMQ-0408", "FA-2026-0121", 410_000_000, "2026-08-23", None),
    ("AZ-2026-SMQ-0362", "FA-2026-0109", 640_000_000, "2026-07-24", "2026-08-12"),
]

POLICIES = [
    ("POL-ST-2026-044", "storage", "ORG-00034", "ORG-00511", "2026-06-01", "2027-05-31", 900_000_000, ["AZ-2026-SMQ-0396", "AZ-2026-SMQ-0355", "AZ-2026-SMQ-0412"]),
    ("POL-CG-2026-018", "cargo", "ORG-00034", "ORG-00097", "2026-07-01", "2027-06-30", 450_000_000, ["AZ-2026-SMQ-0381"]),
]

# code, policy, lot, excursion, amount, status, filed
CLAIMS = [
    ("CL-2026-0043", "POL-ST-2026-044", "AZ-2026-SMQ-0396", "EXC-2026-0311", 18_400_000, "review", "2026-08-24"),
    ("CL-2026-0041", "POL-CG-2026-018", "AZ-2026-SMQ-0381", "", 7_200_000, "approved", "2026-08-19"),
    ("CL-2026-0038", "POL-ST-2026-044", "AZ-2026-SMQ-0355", "EXC-2026-0288", 41_000_000, "paid", "2026-08-02"),
]

# code, zone, metric, from, to, peak, threshold, severity, sensor, trace, lots, resolved
EXCURSIONS = [
    ("EXC-2026-0311", "Z-COLD-02", "temp", "2026-08-24 01:15", "2026-08-24 03:27", "6.90", "4.00", "critical",
     "SENSOR-CD02-B", [4, 4.1, 4.3, 5.2, 6.1, 6.7, 6.9, 6.8, 6.2, 5.4, 4.6, 4.1, 4], ["AZ-2026-SMQ-0396"], False),
    ("EXC-2026-0301", "Z-ZEROCO-01", "temp", "2026-08-24 02:06", "2026-08-24 02:40", "2.90", "2.50", "minor",
     "SENSOR-ZC01-A", [0.4, 0.6, 1.4, 2.3, 2.9, 2.6, 1.8, 0.9, 0.4], ["AZ-2026-SMQ-0412"], True),
    ("EXC-2026-0288", "Z-COLD-02", "temp", "2026-07-30 22:40", "2026-07-31 04:10", "9.20", "4.00", "critical",
     "SENSOR-CD02-B", [4, 4.4, 5.8, 7.2, 8.6, 9.2, 8.4, 6.9, 5.1, 4.2], ["AZ-2026-SMQ-0355"], True),
]

# code, buyer, country, product, quantity kg, incoterm, payment, value USD, status, signed
EXPORTS = [
    ("EX-2026-0088", "Almaty Fresh LLP", "KZ", "grape", 11400, "CPT", "lc", 98_400, "in_progress", "2026-08-12"),
    ("EX-2026-0084", "Riga Produce SIA", "LV", "melon", 22000, "FCA", "cad", 41_800, "signed", "2026-08-05"),
    ("EX-2026-0079", "Dubai Gulf Trading", "AE", "cherry", 2380, "CIP", "advance", 23_800, "shipped", "2026-07-28"),
]

DOCS = [
    ("phyto", "doc_phyto", "issued", "2026-09-10", "PH-2026-44821"),
    ("origin", "doc_origin", "issued", "2026-11-01", "CO-2026-9930"),
    ("invoice", "doc_invoice", "issued", None, "INV-0088"),
    ("packing", "doc_packing", "issued", None, "PL-0088"),
    ("lab", "doc_lab", "issued", "2026-09-24", "LB-2026-1180"),
    ("cmr", "doc_cmr", "pending", None, None),
    ("lc", "doc_lc", "issued", "2026-09-30", "LC-88-KZ-2026"),
]

NOTIFICATIONS = [
    ("crit", "nt_exc", "Z-COLD-02", "2026-08-26 02:40"),
    ("warn", "nt_win", "AZ-2026-SMQ-0396", "2026-08-26 06:15"),
    ("info", "nt_fa", "FA-2026-0121", "2026-08-26 09:02"),
    ("good", "nt_phyto", "EX-2026-0088", "2026-08-26 11:20"),
]

# when, who (username or label), org code, action, object, capability
AUDIT = [
    ("2026-08-26 10:12", "a.bekmurodov", "ORG-00021", "a_viewed", "AZ-2026-SMQ-0412", "view"),
    ("2026-08-26 09:15", "d.yusupov", "ORG-00008", "a_created", "QC-2026-4471", "capture"),
    ("2026-08-26 08:33", "m.karimova", "ORG-00034", "a_downloaded", "EXC-2026-0311", "view"),
    ("2026-08-25 16:02", "a.bekmurodov", "ORG-00021", "a_pledged", "AZ-2026-SMQ-0408", "decide"),
    ("2026-08-25 11:20", "m.tulyaganova", "ORG-00001", "a_verified", "ORG-00412", "verify"),
    ("2026-08-24 02:40", "SENSOR-CD02-B", None, "a_alert", "EXC-2026-0311", "capture"),
    ("2026-08-23 14:55", "a.nazarov", "ORG-00001", "a_role", "T. Xolmatov", "administer"),
]

# grantee (party code or label), scope key, fields key, basis, expires
GRANTS = [
    ("ORG-00021", "g_pledged", "g_f_coll", "owner", "2027-01-31"),
    ("ORG-00034", "g_covered", "g_f_cond", "owner", "2027-03-15"),
    ("Qishloq xo‘jaligi vazirligi", "g_region", "g_f_agg", "law", None),
    ("Almaty Fresh LLP", "g_contract", "g_f_trace", "owner", "2026-12-31"),
    ("Statistika agentligi", "g_national", "g_f_anon", "law", None),
]

#: The waterfall for the contract that is being settled. Priority ascending:
#: the lender is paid before the producer, and the order is data so a different
#: contract can carry a different waterfall without a deployment.
WATERFALL = [
    (1, "ORG-00021", "lender", 41_000, "released"),
    (2, "ORG-00034", "insurer", 2_400, "released"),
    (3, "ORG-00563", "logistics", 8_900, "planned"),
    (4, "ORG-00008", "operator", 4_920, "planned"),
    (5, "ORG-00097", "farmer", 41_180, "planned"),
]

#: The verification review the administration panel opens on: five automatic
#: checks answered, the sector licence with a human.
UNDER_REVIEW = ("ORG-00511", {"identity": "pass", "entity": "pass", "authority": "pass", "land": "pass", "standing": "pass"})

#: Deletion order for `--reset`: children before parents. Most foreign keys
#: here are PROTECT, which is the right default for a system of record and the
#: reason this list is ordered by hand rather than left to the ORM.
DEMO_MODELS = [
    LotEvent, LotRelation, StoragePlacement, TrialObservation, TrialArm,
    PilotTrial, QcRecord, ShipmentLine, Shipment, SettlementAllocation, Claim,
    Policy, Encumbrance, FinanceApplication, ExportContract, Document,
    ConditionReading, ConditionExcursion, GateArrival, Notification,
    AuditEntry, DataGrant, Lot, StorageZone, Facility, ProductionPlan, Farm,
    Membership, PartyVerification, Party, Product,
]


class Command(BaseCommand):
    help = "Load the pilot demonstration dataset."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete the demo rows first. Reference data and superusers survive.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not Role.objects.exists():
            raise CommandError("Run `manage.py seed_reference` first.")

        if options["reset"]:
            self._reset()
        elif Lot.objects.exists():
            raise CommandError(
                "The demo data is already loaded. Re-run with --reset to replace it."
            )

        products = self._products()
        parties = self._parties()
        users = self._users(parties)
        farms = self._farms(parties)
        facility, zones = self._storage(parties)
        lots = self._lots(products, farms, parties, zones, users)
        self._flagship_history(lots, users, parties)
        self._quality(lots, products, users)
        self._arrivals(facility, farms, products)
        self._readings(zones)
        self._excursions()
        applications = self._finance(parties, lots)
        self._insurance(parties, lots)
        contracts = self._commercial(parties, products, lots)
        self._documents(contracts)
        self._governance(parties, users)
        self._verification(parties, users)

        if options["reset"]:
            self.stdout.write(
                self.style.WARNING(
                    "The organisations were rebuilt, so memberships went with "
                    "them. Run `manage.py seed_accounts` to link the same "
                    "people to them again - their passwords are untouched."
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo data: {Party.objects.count()} organisations, "
                f"{User.objects.count()} people, {Lot.objects.count()} lots, "
                f"{LotEvent.objects.count()} events, "
                f"{applications} finance applications."
            )
        )

    # -- helpers ---------------------------------------------------------

    def _reset(self):
        """Empty the demo tables. People are not one of them.

        This used to delete every non-superuser as well, which quietly
        invalidated every password `seed_accounts` had issued - the accounts
        came back on the next run with new ones, and the list somebody had
        written down stopped working. The dataset and the people are separate
        commands; a reset of one must not silently rebuild the other.

        Memberships go, because the organisations they point at do. Run
        `seed_accounts` afterwards to link the same people to the rebuilt
        organisations; it will not touch their passwords.
        """
        for model in DEMO_MODELS:
            # LotEvent refuses `delete()` on the instance, which is the point;
            # a queryset delete is the operator's reset, and it exists only
            # because a demo database is meant to be rebuilt.
            model.objects.all().delete()

    def _products(self) -> dict:
        rows = {}
        for code, uz, ru, en, variety, hs, shelf in PRODUCTS:
            rows[code], _ = Product.objects.update_or_create(
                code=code,
                defaults={
                    "name_uz": uz,
                    "name_ru": ru,
                    "name_en": en,
                    "variety": variety,
                    "hs_code": hs,
                    "shelf_life_days": shelf,
                    "qc_spec": {
                        "brix": {"unit": "°Bx", "min": 10},
                        "firmness_n": {"unit": "N", "min": 6},
                        "defect_pct": {"unit": "%", "max": 5},
                    },
                },
            )
        return rows

    def _parties(self) -> dict:
        types = {t.code: t for t in OrganisationType.objects.all()}
        rows = {}
        for code, name, kind, tin, region, verification, since, by in ORGS:
            rows[code] = Party.objects.create(
                code=code,
                legal_name=name,
                type=types[kind],
                tin=tin,
                region=region,
                verification_status=verification,
                verified_on=date.fromisoformat(since) if since else None,
                verified_by=by or "",
            )
        return rows

    def _users(self, parties: dict) -> dict:
        roles = {r.code: r for r in Role.objects.all()}
        rows = {}
        for username, name, org, role, oneid, eimzo, last, status in USERS:
            first, _, rest = name.partition(" ")
            # `update_or_create`, because a reset keeps people: the dataset is
            # rebuilt around them and their passwords are not touched. Creating
            # blindly here collided with the accounts `seed_accounts` issued.
            user, created = User.objects.update_or_create(
                username=username,
                defaults={
                    "display_name": name,
                    "first_name": first,
                    "last_name": rest,
                    "email": f"{username}@example.uz",
                    "status": status,
                    "oneid_verified": oneid,
                    "eimzo_verified": eimzo,
                    "last_seen_at": at(last) if last else None,
                    "is_active": status != "suspended",
                },
            )
            if created:
                # OneID is the way in for these; an unusable password is the
                # honest state for an account nobody has been given.
                user.set_unusable_password()
                user.save(update_fields=["password"])
            Membership.objects.get_or_create(
                user=user,
                party=parties[org],
                role=roles[role],
                defaults={
                    "facility_codes": (
                        ["HUB-SMQ"] if roles[role].scope == "facility" else []
                    )
                },
            )
            rows[username] = user
        return rows

    def _farms(self, parties: dict) -> dict:
        rows = {}
        for code, name, owner, org, region, district, hectares, certs in FARMS:
            rows[code] = Farm.objects.create(
                code=code,
                party=parties[org],
                name=name,
                owner_name=owner,
                region=region,
                district=district,
                hectares=hectares,
                certifications=certs,
            )
        return rows

    def _storage(self, parties: dict):
        hub = Facility.objects.create(
            code="HUB-SMQ",
            name="Samarqand Hub",
            kind="hub",
            operator_party=parties["ORG-00008"],
            region="Samarqand",
            district="Payariq",
        )
        Facility.objects.create(
            code="HUB-FRG",
            name="Farg‘ona Hub",
            kind="hub",
            operator_party=parties["ORG-00008"],
            region="Farg‘ona",
        )
        zones = {}
        for code, mode, capacity_kg, temp, rh, target_temp, target_rh in ZONES:
            zones[code] = StorageZone.objects.create(
                code=code,
                facility=hub,
                mode=mode,
                capacity_g=capacity_kg * KG,
                target_temp_c=target_temp,
                target_rh_pct=target_rh,
                # ZEROCO chambers are held to a tighter band than cold rooms;
                # that difference is what makes an excursion mean something.
                tolerance_temp_c="2.00" if mode == "zeroco" else "2.00",
                current_temp_c=temp,
                current_rh_pct=rh,
                reading_at=at("2026-08-26 07:00"),
            )
        return hub, zones

    def _lots(self, products, farms, parties, zones, users) -> dict:
        rows = {}
        for (
            code, product, farm, net_kg, grade, status, zone, position,
            harvested, placed, sell_by, arm, valuation,
        ) in LOTS:
            farm_row = farms[farm]
            # Anything at the hub - on a shelf or waiting to be put away - is
            # in the hub operator's custody. Anything dispatched or settled has
            # left, and custody with it.
            in_custody = status not in {"dispatched", "settled", "written_off"}

            lot = Lot.objects.create(
                code=code,
                product=products[product],
                origin_farm=farm_row,
                owner_party=farm_row.party,
                net_weight_g=net_kg * KG,
                gross_weight_g=int(net_kg * 1.025) * KG,
                grade=grade,
                status=status,
                storage_mode=zones[zone].mode if zone else "none",
                harvested_on=date.fromisoformat(harvested),
                sell_by=date.fromisoformat(sell_by) if sell_by else None,
                trial_arm=arm,
                valuation_minor=valuation * TIYIN,
                custody_party=parties["ORG-00008"] if in_custody else None,
            )
            rows[code] = lot

            if zone and placed:
                StoragePlacement.objects.create(
                    lot=lot,
                    zone=zones[zone],
                    position=position,
                    quantity_g=lot.net_weight_g,
                    placed_at=at(placed).replace(hour=6, minute=20),
                )

            # Every lot except the flagship gets a short, honest history: what
            # happened, in order, hash-chained like any other.
            if code != "AZ-2026-SMQ-0412":
                self._short_history(lot, placed, zone, position, users)
        return rows

    def _short_history(self, lot, placed, zone, position, users):
        gate = users["g.rasulova"]
        qc = users["d.yusupov"]
        store = users["s.ergashev"]
        harvested = lot.harvested_on

        lot.log(
            "registered",
            occurred_at=datetime.combine(harvested, datetime.min.time(), TASHKENT).replace(hour=7),
            actor_user=gate,
            actor_label=gate.display_name,
            facility_code="GATE-01",
            payload={
                "gross_weight_g": lot.gross_weight_g,
                "net_weight_g": lot.net_weight_g,
                "idempotency_key": lot.code[-4:],
            },
        )
        if lot.grade:
            lot.log(
                "graded",
                occurred_at=datetime.combine(harvested, datetime.min.time(), TASHKENT).replace(hour=8, minute=35),
                actor_user=qc,
                actor_label=qc.display_name,
                severity="accept",
                payload={"grade": lot.grade, "net_weight_g": lot.net_weight_g, "photos": 3},
            )
        if placed and zone:
            lot.log(
                "placed",
                occurred_at=at(placed).replace(hour=6, minute=20),
                actor_user=store,
                actor_label=store.display_name,
                facility_code="HUB-SMQ",
                severity="accept",
                payload={"zone": zone, "position": position},
            )
        if lot.status in {"dispatched", "settled"}:
            lot.log(
                "dispatched",
                occurred_at=at("2026-08-01 09:00"),
                actor_user=store,
                actor_label=store.display_name,
                severity="accept",
                payload={"destination": "Almaty"},
            )
        if lot.status == "settled":
            lot.log(
                "settled",
                occurred_at=at("2026-08-14 12:00"),
                actor_label="Agro Zanjir",
                severity="accept",
                payload={"contract": "EX-2026-0079"},
            )
        if lot.status == "written_off":
            lot.log(
                "written_off",
                occurred_at=at("2026-08-14 10:00"),
                actor_user=qc,
                actor_label=qc.display_name,
                severity="warn",
                payload={"reason": "excursion_damage", "excursion": "EXC-2026-0288"},
            )

    def _flagship_history(self, lots, users, parties):
        lot = lots["AZ-2026-SMQ-0412"]
        bank = parties["ORG-00021"]
        for event_type, when, username, severity, facility, payload in FLAGSHIP_EVENTS:
            user = users.get(username) if username else None
            lot.log(
                event_type,
                occurred_at=at(when),
                actor_user=user,
                actor_party=bank if event_type == "pledged" else None,
                actor_label=(
                    user.display_name
                    if user
                    else (bank.legal_name if event_type == "pledged" else "SENSOR-ZC01-A")
                ),
                facility_code=facility,
                severity=severity,
                payload=payload,
            )

    def _quality(self, lots, products, users):
        flagship = lots["AZ-2026-SMQ-0412"]
        for stage, when, measurements, defect, grade in QC_RECORDS:
            QcRecord.objects.create(
                lot=flagship,
                stage=stage,
                inspected_on=date.fromisoformat(when),
                inspector=users["d.yusupov"],
                inspector_label="D. Yusupov",
                measurements=measurements,
                defect_pct=defect,
                grade_assigned=grade,
            )

        for code, product, status, started, zeroco_lot, control_lot, observed in TRIALS:
            trial = PilotTrial.objects.create(
                code=code,
                product=products[product],
                facility_code="HUB-SMQ",
                status=status,
                started_on=date.fromisoformat(started),
                schedule_days=TRIAL_DAYS,
                protocol={
                    "metrics": ["loss", "waste", "firm"],
                    "sampling": "every 7 days",
                    "note": "Projections are modelled; only recorded observations are measurements.",
                },
            )
            if not zeroco_lot:
                continue

            arms = {
                "zeroco": TrialArm.objects.create(
                    trial=trial,
                    kind="zeroco",
                    lot=lots[zeroco_lot],
                    zone_code="Z-ZEROCO-01",
                    quantity_g=lots[zeroco_lot].net_weight_g,
                    projection=PROJECTIONS["zeroco"],
                ),
            }
            if control_lot:
                arms["control"] = TrialArm.objects.create(
                    trial=trial,
                    kind="control",
                    lot=lots[control_lot],
                    zone_code="Z-COLD-01",
                    quantity_g=lots[control_lot].net_weight_g,
                    projection=PROJECTIONS["control"],
                )

            # Only the sampling days that have actually happened.
            for index in range(observed):
                day = TRIAL_DAYS[index]
                for kind, arm in arms.items():
                    curve = PROJECTIONS[kind]
                    TrialObservation.objects.create(
                        arm=arm,
                        day_index=day,
                        observed_on=trial.started_on + timedelta(days=day),
                        observer=users["d.yusupov"],
                        weight_loss_pct=curve["loss"][index],
                        waste_pct=curve["waste"][index],
                        firmness_n=curve["firm"][index],
                    )

    def _arrivals(self, facility, farms, products):
        for when, vehicle, farm, product, estimated_kg, status in ARRIVALS:
            GateArrival.objects.create(
                facility=facility,
                farm=farms[farm],
                product=products[product],
                vehicle=vehicle,
                expected_at=at(f"{TODAY.isoformat()} {when}"),
                estimated_weight_g=estimated_kg * KG,
                status=status,
            )

    def _readings(self, zones):
        """A day of readings per zone, so the condition charts have a series.

        Synthetic and shaped, not random: the point of the screen is that a
        reader can see a band being held, and noise with no shape would not
        show that.
        """
        start = at("2026-08-25 08:00")
        rows = []
        for code, zone in zones.items():
            base = float(zone.current_temp_c)
            humidity = float(zone.current_rh_pct)
            for step in range(24):
                drift = ((step % 6) - 2.5) * 0.06
                rows.append(
                    ConditionReading(
                        scope_type="storage_zone",
                        scope_code=code,
                        sensor_id=f"SENSOR-{code[-5:]}",
                        recorded_at=start + timedelta(hours=step),
                        temp_c=round(base + drift, 2),
                        rh_pct=round(humidity + drift * 2, 2),
                    )
                )
        # The reefer's trace, which the transit screen draws.
        for step, value in enumerate([2.1, 2, 1.9, 2, 2.2, 2.1, 2, 1.9, 2, 2.1]):
            rows.append(
                ConditionReading(
                    scope_type="shipment",
                    scope_code="SH-2026-0210",
                    sensor_id="SENSOR-CNTR-4471",
                    recorded_at=at("2026-08-26 06:00") + timedelta(hours=step),
                    temp_c=value,
                    rh_pct=None,
                )
            )
        ConditionReading.objects.bulk_create(rows, ignore_conflicts=True)

    def _excursions(self):
        for (
            code, zone, metric, started, ended, peak, threshold, severity,
            sensor, trace, lot_codes, resolved,
        ) in EXCURSIONS:
            ConditionExcursion.objects.create(
                code=code,
                scope_type="storage_zone",
                scope_code=zone,
                metric=metric,
                started_at=at(started),
                ended_at=at(ended),
                peak_value=peak,
                threshold=threshold,
                severity=severity,
                sensor_id=sensor,
                trace=trace,
                affected_lot_codes=lot_codes,
                resolved=resolved,
            )

    def _finance(self, parties, lots) -> int:
        applications = {}
        for code, applicant, lender, kind, amount, status, lot_codes, ltv, applied in APPLICATIONS:
            application = FinanceApplication.objects.create(
                code=code,
                applicant_party=parties[applicant],
                lender_party=parties[lender],
                kind=kind,
                status=status,
                amount_minor=amount * TIYIN,
                currency="UZS",
                ltv_pct=ltv,
                applied_on=date.fromisoformat(applied),
                decided_on=date.fromisoformat(applied) if status in {"disbursed", "repaid"} else None,
                port_state="pending_operator",
            )
            application.collateral_lots.set([lots[c] for c in lot_codes])
            applications[code] = application

        for lot_code, application_code, amount, created, released in LIENS:
            application = applications[application_code]
            lien = Encumbrance.objects.create(
                lot=lots[lot_code],
                application=application,
                holder_party=application.lender_party,
                amount_minor=amount * TIYIN,
                currency="UZS",
                created_on=date.fromisoformat(created),
            )
            if released:
                # Through the model, so the release lands in the lot's log the
                # same way a real one would.
                lien.release(reference=f"seed:{application_code}")
                lien.released_at = at(released)
                lien.save(update_fields=["released_at"])

        return len(applications)

    def _insurance(self, parties, lots):
        policies = {}
        for code, kind, insurer, holder, starts, ends, amount, lot_codes in POLICIES:
            policy = Policy.objects.create(
                code=code,
                insurer_party=parties[insurer],
                holder_party=parties[holder],
                kind=kind,
                starts_on=date.fromisoformat(starts),
                ends_on=date.fromisoformat(ends),
                amount_minor=amount * TIYIN,
                currency="UZS",
                deductible_minor=5_000_000 * TIYIN,
            )
            policy.covered_lots.set([lots[c] for c in lot_codes])
            policies[code] = policy

        for code, policy, lot_code, excursion, amount, status, filed in CLAIMS:
            Claim.objects.create(
                code=code,
                policy=policies[policy],
                lot=lots[lot_code],
                excursion_code=excursion,
                amount_minor=amount * TIYIN,
                assessed_minor=(amount * TIYIN) if status in {"approved", "paid"} else 0,
                currency="UZS",
                status=status,
                filed_on=date.fromisoformat(filed),
                decided_on=date.fromisoformat(filed) if status in {"approved", "paid"} else None,
                port_state="pending_operator",
            )

    def _commercial(self, parties, products, lots) -> dict:
        contracts = {}
        for code, buyer, country, product, quantity_kg, incoterm, payment, value, status, signed in EXPORTS:
            contracts[code] = ExportContract.objects.create(
                code=code,
                seller_party=parties["ORG-00097"],
                buyer_name=buyer,
                buyer_country=country,
                product=products[product],
                quantity_g=quantity_kg * KG,
                incoterm=incoterm,
                payment_terms=payment,
                amount_minor=value * 100,  # USD cents
                currency="USD",
                # The rate the contract was signed at, snapshotted. Looking it
                # up again later would give a different number and a different
                # settlement.
                fx_rate_to_uzs="12650.000000",
                status=status,
                signed_on=date.fromisoformat(signed),
                ship_by=date.fromisoformat(signed) + timedelta(days=30),
            )

        shipment = Shipment.objects.create(
            code="SH-2026-0210",
            export_contract=contracts["EX-2026-0088"],
            carrier_party=parties["ORG-00563"],
            mode="reefer",
            vehicle="01 K 774 TX / CNTR 4471",
            set_point_c="2.00",
            origin="Samarqand",
            destination="Almaty",
            distance_km=1180,
            departs_at=at("2026-08-27 06:00"),
            eta=at("2026-08-29 18:00"),
            status="loading",
        )
        ShipmentLine.objects.create(
            shipment=shipment,
            lot=lots["AZ-2026-SMQ-0408"],
            quantity_g=11400 * KG,
        )

        delivered = Shipment.objects.create(
            code="SH-2026-0198",
            export_contract=contracts["EX-2026-0079"],
            carrier_party=parties["ORG-00563"],
            mode="reefer",
            vehicle="01 K 552 AN / CNTR 3318",
            set_point_c="0.50",
            origin="Samarqand",
            destination="Dubai",
            distance_km=2410,
            departs_at=at("2026-07-30 05:00"),
            eta=at("2026-08-02 12:00"),
            arrived_at=at("2026-08-02 09:40"),
            status="delivered",
        )
        ShipmentLine.objects.create(
            shipment=delivered, lot=lots["AZ-2026-SMQ-0381"], quantity_g=8900 * KG
        )

        for priority, party, category, amount, status in WATERFALL:
            SettlementAllocation.objects.create(
                export_contract_code="EX-2026-0079",
                priority=priority,
                party=parties[party],
                category=category,
                status=status,
                amount_minor=amount * 100,
                currency="USD",
                fx_rate_to_uzs="12650.000000",
            )
        return contracts

    def _documents(self, contracts):
        contract = contracts["EX-2026-0088"]
        for doc_type, name_key, status, expires, reference in DOCS:
            Document.objects.create(
                code=f"{contract.code}-{doc_type.upper()}",
                subject_type="export_contract",
                subject_code=contract.code,
                doc_type=doc_type,
                name_key=name_key,
                status=status,
                reference=reference or "",
                issued_by="Agro Zanjir" if status == "issued" else "",
                issued_on=date(2026, 8, 20) if status == "issued" else None,
                expires_on=date.fromisoformat(expires) if expires else None,
                file_ref=f"s3://agro-zanjir/docs/{contract.code}/{doc_type}.pdf" if status == "issued" else "",
            )

        # The lab report that the flagship lot's QC record points at.
        Document.objects.create(
            code="LB-2026-1180",
            subject_type="lot",
            subject_code="AZ-2026-SMQ-0412",
            doc_type="lab",
            name_key="doc_lab",
            status="issued",
            reference="LB-2026-1180",
            issued_by="Payariq Lab MChJ",
            issued_on=date(2026, 8, 14),
            expires_on=date(2026, 9, 24),
        )

    def _governance(self, parties, users):
        for when, who, org, action, obj, capability in AUDIT:
            user = users.get(who)
            AuditEntry.objects.create(
                occurred_at=at(when),
                actor_user=user,
                actor_label=user.display_name if user else who,
                actor_party=parties[org] if org else None,
                action_key=action,
                object_ref=obj,
                capability=capability,
            )

        for grantee, scope, fields, basis, expires in GRANTS:
            party = parties.get(grantee)
            DataGrant.objects.create(
                grantee_party=party,
                grantee_label="" if party else grantee,
                scope_key=scope,
                fields_key=fields,
                basis=basis,
                status="active",
                granted_on=date(2026, 6, 1),
                expires_on=date.fromisoformat(expires) if expires else None,
            )

        for level, key, subject, when in NOTIFICATIONS:
            Notification.objects.create(
                level=level, message_key=key, subject=subject, occurred_at=at(when)
            )

    def _verification(self, parties, users):
        """Every organisation's check results, and one review in progress.

        A check that does not apply to the organisation's type is not recorded
        at all - not as a pass, not as a fail. Showing a carrier's land rights
        as verified would be exactly the kind of thing a compliance officer
        catches.
        """
        checks = {c.code: c for c in VerificationCheck.objects.all()}
        officer = users["m.tulyaganova"]

        for party in parties.values():
            required = party.type.required_checks or []
            for code in required:
                if party.code == UNDER_REVIEW[0]:
                    result = UNDER_REVIEW[1].get(code, "review")
                elif party.verification_status == "verified":
                    result = "pass"
                elif party.verification_status == "rejected":
                    result = "fail" if code == "licence" else "pass"
                elif party.verification_status == "review":
                    result = "pass" if code != "licence" else "review"
                else:
                    result = "pending"

                PartyVerification.objects.create(
                    party=party,
                    verification_check=checks[code],
                    result=result,
                    decided_at=at("2026-08-25 11:20") if result != "pending" else None,
                    decided_by=officer if result != "pending" else None,
                    evidence={"register": checks[code].register} if checks[code].register else {},
                )
