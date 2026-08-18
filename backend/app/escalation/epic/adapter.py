from __future__ import annotations

from typing import Any

from app.escalation.audit import append_audit_event


class EpicEscalationAdapter:
    async def dispatch(self, case: dict[str, Any]) -> dict[str, Any]:
        # Epic's provider-facing path is pull/invocation based: CARDINAL persists
        # the active escalation state and the configured CDS Hook reads it when
        # Epic calls the service. This activation is intentionally distinct from
        # proving that Epic actually invoked the public CDS endpoint.
        result = {
            "status": "active",
            "channelState": "READY",
            "cdsHookReady": True,
            "eventId": case.get("eventId"),
            "patientId": case.get("patientId"),
            "encounterId": case.get("encounterId"),
            "effectiveLevel": case.get("effectiveLevel"),
        }
        append_audit_event(
            str(case.get("eventId") or ""),
            "EPIC_ROUTING_ACTIVE",
            detail="CARDINAL Epic escalation state is active and available to the CDS service.",
            data={"effectiveLevel": case.get("effectiveLevel")},
            delivery_result="READY",
        )
        return result


epic_escalation_adapter = EpicEscalationAdapter()
