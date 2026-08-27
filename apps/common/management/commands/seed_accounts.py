"""Create the accounts a pilot is run with, and set their passwords.

Separate from `seed_demo` on purpose. That command owns the *dataset* - lots,
zones, contracts - and `--reset` wipes it. This one owns the *people*, is
idempotent, and never deletes anything: running it twice adds nothing and
changes nothing except passwords, and then only when asked.

Why passwords at all, when OneID is the way in? Because OneID is not connected
yet and a pilot has to be usable before it is. `POST /api/v1/auth/password/`
exists for exactly this - the way in when the state identity provider is
unavailable, which it will be - and these are the accounts that use it.

    manage.py seed_accounts                 # generate a password each, print once
    manage.py seed_accounts --password X    # one shared password (demonstrations)
    manage.py seed_accounts --rotate        # new passwords for accounts that exist

Passwords are printed once and stored only as a hash. There is no way to read
them back afterwards; run `--rotate` if the list is lost.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.crypto import get_random_string

from apps.registry.models import (
    Membership,
    OrganisationType,
    Party,
    PartyVerification,
    Role,
    User,
    VerificationCheck,
)

#: Unambiguous characters only: these get read down a phone and typed on a
#: tablet in a pack-house. No l/1, no O/0.
PASSWORD_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"

#: Organisations the role catalogue needs but the pilot dataset does not carry.
#: Each is verified, because an unverified organisation cannot open a panel and
#: these exist to be signed in as.
#: (code, legal name, type, TIN, region)
EXTRA_PARTIES = [
    ("ORG-00700", "Samarqand Agro Klaster", "aggregator", "310 447 118", "Samarqand"),
    ("ORG-00710", "Almaty Fresh LLP", "buyer", "—", "Almaty"),
    ("ORG-00720", "Qishloq xo‘jaligi vazirligi", "authority", "200 000 100", "Toshkent"),
    ("ORG-00730", "Payariq Pack MChJ", "processor", "311 220 664", "Samarqand"),
    ("ORG-00740", "Sharq Customs Broker", "customs_broker", "312 118 903", "Toshkent"),
]

#: The accounts, grouped by organisation. Three to six roles each: enough to
#: see every panel from every angle it was designed for, and to prove that a
#: role which should not open a panel does not.
#:
#: (username, display name, role, E-IMZO)
#: E-IMZO is the electronic signature; only roles that sign hold one.
ACCOUNTS: list[tuple[str, list[tuple[str, str, str, bool]]]] = [
    (
        "ORG-00001",  # Agro Zanjir - the platform operator
        [
            ("az.owner", "M. Tulyaganova", "platform_owner", True),
            ("az.admin", "A. Nazarov", "platform_admin", True),
            ("az.verify", "L. Saidova", "verification_officer", False),
            ("az.audit", "F. Umarov", "auditor", False),
        ],
    ),
    (
        "ORG-00412",  # Nodir dehqon xo'jaligi - a family farm
        [
            ("farm.owner", "Nodir Sharipov", "org_owner", True),
            ("farm.manager", "H. Sharipova", "farm_manager", False),
            ("farm.recorder", "J. Nazarov", "field_recorder", False),
            ("farm.agro", "U. Qosimov", "agronomist", False),
        ],
    ),
    (
        "ORG-00097",  # Zarafshon Agro - a cooperative that exports directly
        [
            ("coop.owner", "S. Zaripov", "org_owner", True),
            ("coop.commercial", "R. Tursunov", "commercial_manager", True),
            ("coop.docs", "N. Yo‘ldosheva", "documentation_officer", False),
            ("coop.logistics", "K. Rahimov", "logistics_coordinator", False),
        ],
    ),
    (
        "ORG-00008",  # Samarqand Hub Operator - the pack-house and cold store
        [
            ("hub.manager", "I. Abdullayev", "hub_manager", True),
            ("hub.gate", "G. Rasulova", "gate_operator", False),
            ("hub.qc", "D. Yusupov", "qc_inspector", False),
            ("hub.store", "S. Ergashev", "warehouse_operator", False),
            ("hub.pack", "O. Mirzayeva", "packhouse_supervisor", False),
            ("hub.dispatch", "T. Ochilov", "dispatch_coordinator", False),
        ],
    ),
    (
        "ORG-00021",  # Agrobank - inventory and pre-export finance
        [
            ("bank.credit", "A. Bekmurodov", "credit_officer", True),
            ("bank.risk", "V. Sultonova", "risk_analyst", False),
            ("bank.collateral", "E. Xudoyberdiyev", "collateral_inspector", False),
            ("bank.approver", "Sh. Ismoilov", "credit_approver", True),
        ],
    ),
    (
        "ORG-00034",  # Uzagrosug'urta - storage and cargo cover
        [
            ("ins.underwriter", "Z. Alimova", "underwriter", True),
            ("ins.claims", "M. Karimova", "claims_adjuster", True),
            ("ins.admin", "P. Toshpo‘latov", "org_admin", False),
        ],
    ),
    (
        "ORG-00588",  # Payariq Lab - accredited laboratory
        [
            ("lab.tech", "N. Ergasheva", "lab_technician", False),
            ("lab.approver", "B. Sodiqov", "lab_approver", True),
            ("lab.owner", "X. Yusupova", "org_owner", True),
        ],
    ),
    (
        "ORG-00563",  # Uztrans Logistic - the carrier
        [
            ("carrier.dispatch", "B. Qodirov", "dispatcher", False),
            ("carrier.driver", "A. Tursunov", "driver", False),
            ("carrier.owner", "M. Rustamov", "org_owner", True),
        ],
    ),
    (
        "ORG-00700",  # Samarqand Agro Klaster - an aggregator
        [
            ("aggr.owner", "D. Qurbonov", "org_owner", True),
            ("aggr.manager", "S. Aliyeva", "farm_manager", False),
            ("aggr.commercial", "F. Boboyev", "commercial_manager", True),
        ],
    ),
    (
        "ORG-00710",  # Almaty Fresh - the buyer on the other side of the contract
        [
            ("buyer.procure", "A. Serikov", "procurement_manager", True),
            ("buyer.quality", "G. Nurlanova", "quality_manager", False),
            ("buyer.viewer", "T. Bekova", "org_viewer", False),
        ],
    ),
    (
        "ORG-00720",  # The ministry - reads, never writes
        [
            ("gov.analyst", "R. Xolmatova", "ministry_analyst", False),
            ("gov.regional", "S. Jo‘rayev", "regional_officer", False),
            ("gov.inspector", "N. Qodirova", "field_inspector", False),
            ("gov.regulator", "A. Salimov", "regulator", False),
        ],
    ),
    (
        "ORG-00730",  # Payariq Pack - a processor
        [
            ("proc.owner", "Q. Ermatov", "org_owner", True),
            ("proc.pack", "L. Xasanova", "packhouse_supervisor", False),
            ("proc.qc", "I. Rasulov", "qc_inspector", False),
        ],
    ),
    (
        "ORG-00740",  # A customs broker
        [
            ("broker.owner", "Sh. Nazirov", "org_owner", True),
            ("broker.docs", "M. Yusupova", "documentation_officer", False),
            ("broker.logistics", "A. Karimov", "logistics_coordinator", False),
        ],
    ),
]

#: Which panel each role opens, for the operator handing these out. The web
#: client's `lib/panels.ts` is the authority; this is a convenience, and the
#: `-` rows are honest: those roles have no panel of their own in phase 1 and
#: see the public one.
PANEL_BY_ROLE = {
    "platform_owner": "08 Administration (and every other panel)",
    "platform_admin": "08 Administration (and every other panel)",
    "verification_officer": "08 Administration (and every other panel)",
    "auditor": "08 Administration (and every other panel)",
    "org_owner": "the panel of its organisation's kind",
    "org_admin": "the panel of its organisation's kind",
    "org_viewer": "07 Public only",
    "farm_manager": "01 Producer",
    "field_recorder": "01 Producer",
    "agronomist": "01 Producer",
    "hub_manager": "02 Hub operations, 03 ZEROCO trial",
    "gate_operator": "02 Hub operations",
    "qc_inspector": "02 Hub operations, 03 ZEROCO trial",
    "packhouse_supervisor": "02 Hub operations",
    "warehouse_operator": "02 Hub operations",
    "dispatch_coordinator": "02 Hub operations",
    "commercial_manager": "06 Export",
    "documentation_officer": "06 Export",
    "logistics_coordinator": "06 Export",
    "credit_officer": "04 Bank",
    "risk_analyst": "04 Bank",
    "collateral_inspector": "04 Bank",
    "credit_approver": "04 Bank",
    "underwriter": "05 Insurer",
    "claims_adjuster": "05 Insurer",
    "lab_technician": "03 ZEROCO trial",
    "lab_approver": "03 ZEROCO trial",
    "dispatcher": "07 Public only",
    "driver": "07 Public only",
    "procurement_manager": "07 Public only",
    "quality_manager": "07 Public only",
    "ministry_analyst": "07 Public only",
    "regional_officer": "07 Public only",
    "field_inspector": "07 Public only",
    "regulator": "07 Public only",
}


class Command(BaseCommand):
    help = "Create the pilot's accounts and set their passwords."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            help=(
                "One password for every account. For a demonstration on a "
                "laptop; never for anything reachable from the internet."
            ),
        )
        parser.add_argument(
            "--rotate",
            action="store_true",
            help="Also set a new password on accounts that already exist.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not Role.objects.exists():
            raise CommandError("Run `manage.py seed_reference` first.")
        if not Party.objects.filter(code="ORG-00001").exists():
            raise CommandError("Run `manage.py seed_demo` first.")

        shared = options.get("password")
        rotate = options["rotate"]

        self._extra_parties()
        # The laboratory's accounts exist to be signed in as, and an
        # unverified organisation cannot open a panel. The two organisations
        # the administration panel's verification queue is *about* - Urgut and
        # Uztrans - are deliberately left as they are.
        self._verify("ORG-00588")

        roles = {r.code: r for r in Role.objects.all()}
        rows = []

        for party_code, accounts in ACCOUNTS:
            party = Party.objects.get(code=party_code)
            for username, display_name, role_code, eimzo in accounts:
                user, created = User.objects.get_or_create(
                    username=username,
                    defaults={
                        "display_name": display_name,
                        "email": f"{username.replace('.', '-')}@agrozanjir.uz",
                        "status": User.Status.ACTIVE,
                        "oneid_verified": True,
                        "eimzo_verified": eimzo,
                    },
                )
                password = ""
                if created or rotate:
                    password = shared or get_random_string(10, PASSWORD_ALPHABET)
                    user.set_password(password)
                    user.save(update_fields=["password"])

                Membership.objects.get_or_create(
                    user=user,
                    party=party,
                    role=roles[role_code],
                    defaults={
                        "facility_codes": (
                            ["HUB-SMQ"] if roles[role_code].scope == "facility" else []
                        )
                    },
                )
                rows.append(
                    {
                        "username": username,
                        "password": password or "(unchanged)",
                        "name": display_name,
                        "org": party.legal_name,
                        "role": role_code,
                        "panel": PANEL_BY_ROLE.get(role_code, "-"),
                    }
                )

        self._print(rows)

    # -- helpers ---------------------------------------------------------

    def _extra_parties(self):
        types = {t.code: t for t in OrganisationType.objects.all()}
        for code, name, kind, tin, region in EXTRA_PARTIES:
            party, created = Party.objects.get_or_create(
                code=code,
                defaults={
                    "legal_name": name,
                    "type": types[kind],
                    "tin": tin,
                    "region": region,
                    "verification_status": Party.Verification.PENDING,
                },
            )
            if created:
                self._verify(code)

    def _verify(self, code: str):
        """Pass every check this kind of organisation has to clear.

        Through the same rows the administration panel reads, not by setting
        the status directly: the status is a cache of the checks, and a cache
        written by hand is how the two stop agreeing.
        """
        party = Party.objects.select_related("type").get(code=code)
        checks = {c.code: c for c in VerificationCheck.objects.all()}
        for check_code in party.type.required_checks or []:
            PartyVerification.objects.update_or_create(
                party=party,
                verification_check=checks[check_code],
                defaults={"result": PartyVerification.Result.PASS},
            )
        from apps.registry.views import recompute_status

        party.verification_status = recompute_status(party)
        party.verified_by = party.verified_by or "seed_accounts"
        party.save(update_fields=["verification_status", "verified_by", "updated_at"])

    def _print(self, rows: list[dict]):
        width = {
            key: max(len(key), max(len(str(r[key])) for r in rows))
            for key in ("username", "password", "name", "org", "role", "panel")
        }
        header = "  ".join(k.upper().ljust(width[k]) for k in width)
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(header))
        self.stdout.write("-" * len(header))
        for row in rows:
            self.stdout.write("  ".join(str(row[k]).ljust(width[k]) for k in width))
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "Printed once and stored only as a hash - there is no way to read a "
                "password back. Re-run with --rotate if this list is lost, and "
                "rotate before anything of this is reachable from the internet."
            )
        )
