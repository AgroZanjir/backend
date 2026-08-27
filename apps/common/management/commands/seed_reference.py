"""Seed the reference data: capabilities, roles, organisation types, checks.

This is not demo data. Every row here is part of the product - the ten
capabilities the API checks, the thirty-seven roles the product speaks in, the
thirteen kinds of organisation and the six verification checks. A deployment
with an empty database and no demo data still needs all of it, which is why it
is a separate command from `seed_demo`.

Idempotent: run it after every deployment. `update_or_create` on the code
means an edited label reaches production without a migration.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.registry.models import Capability, OrganisationType, Role, VerificationCheck

CAPABILITIES = [
    ("view", "c_view"),
    ("capture", "c_capture"),
    ("approve", "c_approve"),
    ("transact", "c_transact"),
    ("sign", "c_sign"),
    ("decide", "c_decide"),
    ("administer", "c_admin"),
    ("verify", "c_verify"),
    ("configure", "c_config"),
    ("audit", "c_audit"),
]

# (code, label key, icon, required checks, licence register)
ORG_TYPES = [
    ("farmer", "ot_farmer", "harvest", ["identity", "entity", "authority", "land", "standing"], ""),
    ("cooperative", "ot_coop", "farms", ["identity", "entity", "authority", "land", "standing"], ""),
    ("aggregator", "ot_aggr", "lots", ["identity", "entity", "authority", "standing"], ""),
    ("operator", "ot_operator", "ops", ["identity", "entity", "authority", "licence", "standing"], "Food safety register"),
    ("processor", "ot_processor", "grade", ["identity", "entity", "authority", "licence", "standing"], "Food safety register"),
    ("laboratory", "ot_lab", "qc", ["identity", "entity", "authority", "licence", "standing"], "Accreditation register"),
    ("exporter", "ot_exporter", "ship", ["identity", "entity", "authority", "licence", "standing"], "Export register"),
    ("buyer", "ot_buyer", "source", ["identity", "entity", "authority", "standing"], ""),
    ("carrier", "ot_carrier", "disp", ["identity", "entity", "authority", "licence", "standing"], "Transport licence register"),
    ("bank", "ot_bank", "port", ["identity", "entity", "authority", "licence", "standing"], "Central Bank register"),
    ("insurer", "ot_insurer", "claims", ["identity", "entity", "authority", "licence", "standing"], "Insurance supervision"),
    ("customs_broker", "ot_broker", "cust", ["identity", "entity", "authority", "licence", "standing"], "Customs broker register"),
    ("authority", "ot_authority", "pub", ["identity", "entity", "authority"], ""),
]

# (check, label key, register it reads, auto or manual)
CHECKS = [
    ("identity", "v_identity", "OneID", "auto"),
    ("entity", "v_entity", "Business register", "auto"),
    ("authority", "v_authority", "E-IMZO", "auto"),
    ("land", "v_land", "E-IJARA / Cadastre", "auto"),
    ("licence", "v_licence", "", "manual"),
    ("standing", "v_standing", "Tax / court register", "auto"),
]

# (group key, [(role, label key, capabilities, scope)])
ROLE_GROUPS = [
    ("rg_platform", [
        ("platform_owner", "r_pown", ["view", "administer", "verify", "configure", "audit"], "platform"),
        ("platform_admin", "r_padmin", ["view", "administer", "configure", "audit"], "platform"),
        ("verification_officer", "r_verif", ["view", "verify"], "platform"),
        ("support_agent", "r_support", ["view"], "platform"),
        ("auditor", "r_auditor", ["view", "audit"], "platform"),
    ]),
    ("rg_org", [
        ("org_owner", "r_oown", ["view", "transact", "sign", "administer"], "org"),
        ("org_admin", "r_oadmin", ["view", "administer"], "org"),
        ("org_member", "r_omember", ["view"], "org"),
        ("org_viewer", "r_oviewer", ["view"], "org"),
    ]),
    ("rg_producer", [
        ("farm_manager", "r_fmgr", ["view", "capture", "transact"], "org"),
        ("field_recorder", "r_frec", ["view", "capture"], "facility"),
        ("agronomist", "r_agro", ["view", "capture", "configure"], "org"),
    ]),
    ("rg_hub", [
        ("hub_manager", "r_hmgr", ["view", "capture", "approve", "configure"], "facility"),
        ("gate_operator", "r_gate", ["view", "capture"], "facility"),
        ("qc_inspector", "r_qc", ["view", "capture", "approve"], "facility"),
        ("packhouse_supervisor", "r_pack", ["view", "capture", "approve"], "facility"),
        ("warehouse_operator", "r_wh", ["view", "capture"], "facility"),
        ("dispatch_coordinator", "r_disp", ["view", "capture", "approve"], "facility"),
    ]),
    ("rg_export", [
        ("commercial_manager", "r_comm", ["view", "transact", "sign"], "org"),
        ("documentation_officer", "r_doc", ["view", "capture", "transact"], "org"),
        ("logistics_coordinator", "r_logco", ["view", "transact"], "org"),
    ]),
    ("rg_bank", [
        ("credit_officer", "r_credit", ["view", "transact"], "org"),
        ("risk_analyst", "r_risk", ["view", "audit"], "org"),
        ("collateral_inspector", "r_collin", ["view", "approve"], "org"),
        ("credit_approver", "r_capp", ["view", "decide", "sign"], "org"),
    ]),
    ("rg_insurer", [
        ("underwriter", "r_uw", ["view", "transact", "sign"], "org"),
        ("claims_adjuster", "r_claims", ["view", "decide"], "org"),
    ]),
    ("rg_carrier", [
        ("dispatcher", "r_cdisp", ["view", "transact"], "org"),
        ("driver", "r_driver", ["view", "capture"], "facility"),
    ]),
    ("rg_lab", [
        ("lab_technician", "r_labtech", ["view", "capture"], "org"),
        ("lab_approver", "r_labapp", ["view", "approve", "sign"], "org"),
    ]),
    ("rg_buyer", [
        ("procurement_manager", "r_proc", ["view", "transact", "sign"], "org"),
        ("quality_manager", "r_qmgr", ["view", "approve"], "org"),
    ]),
    ("rg_authority", [
        ("ministry_analyst", "r_manalyst", ["view", "audit"], "national"),
        ("regional_officer", "r_regoff", ["view", "audit"], "region"),
        ("field_inspector", "r_finsp", ["view", "capture", "approve"], "region"),
        ("regulator", "r_reg", ["view", "audit"], "national"),
    ]),
]


class Command(BaseCommand):
    help = "Create or update the capability, role, organisation-type and check catalogues."

    @transaction.atomic
    def handle(self, *args, **options):
        capabilities = {}
        for order, (code, label) in enumerate(CAPABILITIES):
            capabilities[code], _ = Capability.objects.update_or_create(
                code=code, defaults={"label_key": label, "sort_order": order}
            )

        for order, (code, label, icon, checks, register) in enumerate(ORG_TYPES):
            OrganisationType.objects.update_or_create(
                code=code,
                defaults={
                    "label_key": label,
                    "icon": icon,
                    "required_checks": checks,
                    "licence_register": register,
                    "sort_order": order,
                },
            )

        for order, (code, label, register, mode) in enumerate(CHECKS):
            VerificationCheck.objects.update_or_create(
                code=code,
                defaults={
                    "label_key": label,
                    "register": register,
                    "mode": mode,
                    "sort_order": order,
                },
            )

        order = 0
        for group_key, roles in ROLE_GROUPS:
            for code, label, caps, scope in roles:
                role, _ = Role.objects.update_or_create(
                    code=code,
                    defaults={
                        "label_key": label,
                        "group_key": group_key,
                        "scope": scope,
                        "sort_order": order,
                    },
                )
                role.capabilities.set([capabilities[c] for c in caps])
                order += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Reference data: {Capability.objects.count()} capabilities, "
                f"{Role.objects.count()} roles, {OrganisationType.objects.count()} "
                f"organisation types, {VerificationCheck.objects.count()} checks."
            )
        )
