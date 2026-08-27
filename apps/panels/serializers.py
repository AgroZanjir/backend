"""The shapes the panels read.

Plain builders rather than DRF serializers, deliberately: every one of these is
a composition across two or more clusters with its own join strategy, and a
`ModelSerializer` per screen would be a class hierarchy pretending to be a
query plan. Each function here takes already-fetched objects and returns the
JSON one screen needs.

Naming is the API's, not the screen's: `net_weight_g`, not `net`. The web
client maps to its own terse view model in one module, so a rename here costs
one file there rather than forty.
"""

from __future__ import annotations

from apps.storage.models import StoragePlacement


def product_payload(product) -> dict:
    return {
        "code": product.code,
        "name_uz": product.name_uz,
        "name_ru": product.name_ru,
        "name_en": product.name_en,
        "variety": product.variety,
        "hs_code": product.hs_code,
        "shelf_life_days": product.shelf_life_days,
    }


def facility_payload(facility) -> dict:
    return {
        "code": facility.code,
        "name": facility.name,
        "kind": facility.kind,
        "region": facility.region,
    }


def zone_payload(zone) -> dict:
    """A room, with live fill and both set points.

    `used_g` is summed from open placements rather than stored, so a zone
    cannot drift away from what is actually in it.
    """
    return {
        "code": zone.code,
        "facility": zone.facility.code,
        "mode": zone.mode,
        "capacity_g": zone.capacity_g,
        "used_g": zone.used_g,
        "temp_c": zone.current_temp_c,
        "rh_pct": zone.current_rh_pct,
        "target_temp_c": zone.target_temp_c,
        "target_rh_pct": zone.target_rh_pct,
        "off_band": zone.off_band,
        "reading_at": zone.reading_at,
    }


def farm_payload(farm, *, lot_count: int | None = None) -> dict:
    return {
        "code": farm.code,
        "name": farm.name,
        "owner_name": farm.owner_name,
        "party": farm.party.code,
        "region": farm.region,
        "district": farm.district,
        "hectares": farm.hectares,
        "certifications": farm.certifications,
        "lot_count": lot_count if lot_count is not None else farm.lots.count(),
    }


def lot_payload(lot, *, placement=None, pledged: bool | None = None) -> dict:
    """One row of any lot table on any panel.

    `placement` and `pledged` are passed in by the view, which fetches them in
    one query for the whole page. Left to itself this function would issue two
    queries per lot, and the hub's storage screen lists a hundred.
    """
    if placement is None:
        placement = (
            StoragePlacement.objects.filter(lot=lot, removed_at__isnull=True)
            .select_related("zone")
            .first()
        )
    if pledged is None:
        pledged = lot.encumbrances.filter(released_at__isnull=True).exists()

    return {
        "code": lot.code,
        "product": lot.product.code,
        "farm": lot.origin_farm.code if lot.origin_farm else None,
        "farm_name": lot.origin_farm.name if lot.origin_farm else "",
        "owner_party": lot.owner_party.code,
        "owner_name": lot.owner_party.legal_name,
        "net_weight_g": lot.net_weight_g,
        "grade": lot.grade,
        "status": lot.status,
        "storage_mode": lot.storage_mode,
        "zone": placement.zone.code if placement else None,
        "position": placement.position if placement else "",
        "harvested_on": lot.harvested_on,
        "placed_at": placement.placed_at if placement else None,
        "sell_by": lot.sell_by,
        # An overlay, never a status (rule 3). The lot is still stored, still
        # reserved, still shippable - it just cannot leave.
        "pledged": pledged,
        "trial_arm": lot.trial_arm,
        "valuation_minor": lot.valuation_minor,
        "valuation_currency": lot.valuation_currency,
    }


def event_payload(event) -> dict:
    return {
        "sequence": event.sequence,
        "type": event.event_type,
        "occurred_at": event.occurred_at,
        "actor": event.actor_label
        or (event.actor_user.display_name if event.actor_user else ""),
        "facility": event.facility_code,
        "payload": event.payload,
        "severity": event.severity,
        "hash": event.hash,
        "prev_hash": event.prev_hash,
    }


def qc_payload(record) -> dict:
    return {
        "stage": record.stage,
        "inspected_on": record.inspected_on,
        "inspector": record.inspector_label
        or (record.inspector.display_name if record.inspector else ""),
        "measurements": record.measurements,
        "grade_assigned": record.grade_assigned,
        "defect_pct": record.defect_pct,
        "passed": record.passed,
    }


def document_payload(document) -> dict:
    return {
        "code": document.code,
        "subject_type": document.subject_type,
        "subject_code": document.subject_code,
        "type": document.doc_type,
        "name_key": document.name_key,
        "status": document.status,
        "reference": document.reference,
        "issued_by": document.issued_by,
        "issued_on": document.issued_on,
        "expires_on": document.expires_on,
        "expired": document.is_expired,
    }


def excursion_payload(excursion) -> dict:
    return {
        "code": excursion.code,
        "scope_type": excursion.scope_type,
        "scope_code": excursion.scope_code,
        "metric": excursion.metric,
        "started_at": excursion.started_at,
        "ended_at": excursion.ended_at,
        "duration_minutes": excursion.duration_minutes,
        "peak_value": excursion.peak_value,
        "threshold": excursion.threshold,
        "severity": excursion.severity,
        "sensor_id": excursion.sensor_id,
        "trace": excursion.trace,
        "lots": excursion.affected_lot_codes,
        "resolved": excursion.resolved,
    }


def application_payload(application) -> dict:
    return {
        "code": application.code,
        "applicant": application.applicant_party.legal_name,
        "applicant_code": application.applicant_party.code,
        "lender": application.lender_party.legal_name,
        "kind": application.kind,
        "status": application.status,
        "amount_minor": application.amount_minor,
        "currency": application.currency,
        "ltv_pct": application.ltv_pct,
        "applied_on": application.applied_on,
        "decided_on": application.decided_on,
        "lots": [lot.code for lot in application.collateral_lots.all()],
        "port_state": application.port_state,
        "port_reference": application.port_reference,
    }


def lien_payload(lien) -> dict:
    return {
        "lot": lien.lot.code,
        "application": lien.application.code,
        "holder": lien.holder_party.legal_name,
        "amount_minor": lien.amount_minor,
        "currency": lien.currency,
        "created_on": lien.created_on,
        "released_at": lien.released_at,
        "status": "active" if lien.is_active else "released",
    }


def claim_payload(claim) -> dict:
    return {
        "code": claim.code,
        "policy": claim.policy.code,
        "kind": claim.policy.kind,
        "holder": claim.policy.holder_party.legal_name,
        "lot": claim.lot.code if claim.lot else None,
        "excursion": claim.excursion_code or None,
        "status": claim.status,
        "amount_minor": claim.amount_minor,
        "assessed_minor": claim.assessed_minor,
        "currency": claim.currency,
        "filed_on": claim.filed_on,
        "decided_on": claim.decided_on,
    }


def policy_payload(policy) -> dict:
    return {
        "code": policy.code,
        "kind": policy.kind,
        "status": policy.status,
        "insurer": policy.insurer_party.legal_name,
        "holder": policy.holder_party.legal_name,
        "starts_on": policy.starts_on,
        "ends_on": policy.ends_on,
        "amount_minor": policy.amount_minor,
        "currency": policy.currency,
    }


def export_payload(contract) -> dict:
    shipment = contract.shipments.first()
    return {
        "code": contract.code,
        "buyer": contract.buyer_name,
        "country": contract.buyer_country,
        "product": contract.product.code,
        "quantity_g": contract.quantity_g,
        "incoterm": contract.incoterm,
        "payment_terms": contract.payment_terms,
        "amount_minor": contract.amount_minor,
        "currency": contract.currency,
        "status": contract.status,
        "shipment": shipment.code if shipment else None,
        "signed_on": contract.signed_on,
        "ship_by": contract.ship_by,
    }


def shipment_payload(shipment, *, temps: list | None = None) -> dict:
    return {
        "code": shipment.code,
        "export_contract": shipment.export_contract.code
        if shipment.export_contract
        else None,
        "carrier": shipment.carrier_party.legal_name if shipment.carrier_party else "",
        "mode": shipment.mode,
        "vehicle": shipment.vehicle,
        "set_point_c": shipment.set_point_c,
        "route": shipment.route,
        "origin": shipment.origin,
        "destination": shipment.destination,
        "distance_km": shipment.distance_km,
        "departs_at": shipment.departs_at,
        "eta": shipment.eta,
        "arrived_at": shipment.arrived_at,
        "status": shipment.status,
        "temps": temps if temps is not None else [],
        "lines": [
            {"lot": line.lot.code, "quantity_g": line.quantity_g}
            for line in shipment.lines.select_related("lot")
        ],
    }


def trial_summary_payload(trial) -> dict:
    from django.utils import timezone

    day = 0
    if trial.started_on:
        end = trial.completed_on or timezone.localdate()
        day = (end - trial.started_on).days
    return {
        "code": trial.code,
        "product": trial.product.code,
        "status": trial.status,
        "started_on": trial.started_on,
        "day": day,
        "arms": trial.arms.count(),
        "observations": sum(arm.observations.count() for arm in trial.arms.all()),
    }


def trial_detail_payload(trial) -> dict:
    """The comparison screen's whole payload.

    Measured points and the modelled curve are returned as separate keys on
    purpose. The screen draws the measured part solid and the projection
    dashed; if this endpoint merged them, that distinction would be gone by the
    time anyone looked at the chart.
    """
    arms = {}
    for arm in trial.arms.select_related("lot").prefetch_related("observations"):
        observations = sorted(arm.observations.all(), key=lambda o: o.day_index)
        arms[arm.kind] = {
            "lot": arm.lot.code,
            "zone": arm.zone_code,
            "quantity_g": arm.quantity_g,
            "projection": arm.projection,
            "observations": [
                {
                    "day_index": o.day_index,
                    "observed_on": o.observed_on,
                    "weight_loss_pct": o.weight_loss_pct,
                    "waste_pct": o.waste_pct,
                    "firmness_n": o.firmness_n,
                    "colour_score": o.colour_score,
                    "markdown_pct": o.markdown_pct,
                }
                for o in observations
            ],
        }

    return {
        "code": trial.code,
        "product": trial.product.code,
        "status": trial.status,
        "started_on": trial.started_on,
        "facility": trial.facility_code,
        "schedule_days": trial.schedule_days,
        "observed_points": trial.observed_points,
        "protocol": trial.protocol,
        "arms": arms,
    }


def arrival_payload(arrival) -> dict:
    return {
        "id": str(arrival.id),
        "expected_at": arrival.expected_at,
        "vehicle": arrival.vehicle,
        "farm": arrival.farm.code if arrival.farm else None,
        "farm_name": arrival.farm.name if arrival.farm else "",
        "product": arrival.product.code,
        "estimated_weight_g": arrival.estimated_weight_g,
        "status": arrival.status,
        "lot_code": arrival.lot_code,
        "facility": arrival.facility.code,
    }


def organisation_payload(party, *, user_count: int | None = None) -> dict:
    return {
        "code": party.code,
        "name": party.legal_name,
        "type": party.type.code,
        "tin": party.tin,
        "region": party.region,
        "users": user_count if user_count is not None else party.memberships.count(),
        "status": party.verification_status,
        "verified_on": party.verified_on,
        "verified_by": party.verified_by,
    }


def platform_user_payload(user) -> dict:
    membership = next(iter(user.memberships.all()), None)
    return {
        "id": str(user.pk),
        "name": user.display_name or user.get_username(),
        "initials": user.initials,
        "org": membership.party.code if membership else None,
        "org_name": membership.party.legal_name if membership else "",
        "role": membership.role.code if membership else "",
        "oneid": user.oneid_verified,
        "eimzo": user.eimzo_verified,
        "last_seen_at": user.last_seen_at,
        "status": user.status,
    }


def audit_payload(entry) -> dict:
    return {
        "occurred_at": entry.occurred_at,
        "who": entry.actor_label
        or (entry.actor_user.display_name if entry.actor_user else ""),
        "org": entry.actor_party.legal_name if entry.actor_party else "—",
        "action_key": entry.action_key,
        "object_ref": entry.object_ref,
        "capability": entry.capability,
    }


def grant_payload(grant) -> dict:
    return {
        "id": str(grant.id),
        "org": grant.grantee_party.legal_name
        if grant.grantee_party
        else grant.grantee_label,
        "scope_key": grant.scope_key,
        "fields_key": grant.fields_key,
        "basis": grant.basis,
        "status": grant.status,
        "expires_on": grant.expires_on,
    }


def notification_payload(notification) -> dict:
    return {
        "id": str(notification.id),
        "level": notification.level,
        "message_key": notification.message_key,
        "subject": notification.subject,
        "occurred_at": notification.occurred_at,
        "read_at": notification.read_at,
    }
