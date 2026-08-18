from __future__ import annotations

from typing import Any

from app.escalation.audit import append_audit_event
from app.escalation.repository import escalation_repository


def _event_for_card(card_uuid: str) -> str | None:
    card_uuid = str(card_uuid or "").strip()
    if not card_uuid:
        return None
    for case in escalation_repository.list_cases():
        for item in case.get("epicCdsCards") or []:
            if isinstance(item, dict) and str(item.get("cardUuid") or "") == card_uuid:
                return str(case.get("eventId") or "") or None
    return None


def _legacy_event_id(payload: dict[str, Any]) -> str:
    extension = payload.get("extension") if isinstance(payload.get("extension"), dict) else {}
    return str(
        payload.get("eventId")
        or payload.get("cardinalEventId")
        or extension.get("eventId")
        or ""
    ).strip()


def record_cds_feedback(payload: dict[str, Any], *, correlation_id: str | None = None) -> dict[str, Any]:
    feedback = payload.get("feedback") if isinstance(payload.get("feedback"), list) else []
    matched: list[str] = []

    # Current CDS Hooks feedback format: feedback[].card references the UUID of
    # the card previously returned by the service.
    for item in feedback:
        if not isinstance(item, dict):
            continue
        event_id = _event_for_card(str(item.get("card") or ""))
        if not event_id:
            continue
        append_audit_event(
            event_id,
            "EPIC_CDS_FEEDBACK_RECEIVED",
            detail="Epic CDS feedback was received for a CARDINAL card.",
            data={"feedback": item, "correlationId": correlation_id},
            delivery_result="RECEIVED",
        )
        matched.append(event_id)

    # Compatibility with the first CARDINAL endpoint contract.
    if not matched:
        event_id = _legacy_event_id(payload)
        case = escalation_repository.get(event_id) if event_id else None
        if case:
            append_audit_event(
                event_id,
                "EPIC_CDS_FEEDBACK_RECEIVED",
                detail="Epic CDS feedback was received.",
                data={"feedback": payload, "correlationId": correlation_id},
                delivery_result="RECEIVED",
            )
            matched.append(event_id)

    return {
        "accepted": True,
        "matched": bool(matched),
        "eventIds": sorted(set(matched)),
    }
