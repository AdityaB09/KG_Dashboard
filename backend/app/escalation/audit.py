from __future__ import annotations

from typing import Any

from app.escalation.models import now_iso
from app.escalation.repository import EscalationRepository, escalation_repository


def append_audit_event(
    event_id: str,
    event_type: str,
    *,
    detail: str | None = None,
    data: dict[str, Any] | None = None,
    actor: str | None = None,
    actor_role: str | None = None,
    previous_state: str | None = None,
    new_state: str | None = None,
    reason: str | None = None,
    external_vendor_id: str | None = None,
    delivery_result: str | None = None,
    http_status: int | None = None,
    repository: EscalationRepository = escalation_repository,
) -> dict[str, Any]:
    """Append an auditable event with stable case identifiers.

    The timeline remains embedded in the EscalationCase so the existing storage
    model is preserved. No OAuth/CDS bearer token is ever copied into audit data.
    """
    case = repository.get(event_id) or {}
    supplied = dict(data or {})
    event = {
        "type": str(event_type),
        "at": now_iso(),
        "eventId": str(case.get("eventId") or event_id),
        "correlationId": case.get("correlationId"),
        "patientId": case.get("patientId"),
        "encounterId": case.get("encounterId"),
        "episodeId": case.get("episodeId"),
        "incidentId": case.get("incidentId"),
        "provider": case.get("provider"),
        "actor": actor or supplied.get("actor"),
        "actorRole": actor_role or supplied.get("actorRole"),
        "previousState": previous_state or supplied.get("previousState"),
        "newState": new_state or supplied.get("newState"),
        "reason": reason or supplied.get("reason"),
        "externalVendorId": external_vendor_id or supplied.get("externalVendorId"),
        "deliveryResult": delivery_result or supplied.get("deliveryResult"),
        "httpStatus": http_status if http_status is not None else supplied.get("httpStatus"),
        "detail": detail,
        "data": supplied,
    }

    def mutate(value: dict[str, Any]) -> None:
        value.setdefault("timeline", []).append(event)

    repository.update(event_id, mutate)
    return event
