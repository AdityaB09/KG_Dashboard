from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.escalation.levels import EscalationLevel, level_label, level_role, normalize_level


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_event_id() -> str:
    return f"esc-{uuid4().hex[:20]}"


def new_correlation_id() -> str:
    return f"corr-{uuid4().hex}"


def public_case(case: dict[str, Any]) -> dict[str, Any]:
    """Remove internal-only routing references before returning a case to the browser."""
    result = dict(case)
    result.pop("routingContext", None)
    return result


def build_case(
    *,
    event_id: str,
    episode_id: str,
    incident_id: str,
    scenario_id: str,
    provider: str,
    patient_id: str | None,
    encounter_id: str | None,
    model_suggested_level: EscalationLevel,
    policy_minimum_level: EscalationLevel,
    effective_level: EscalationLevel,
    model_rationale: str,
    model_confidence: str,
    model_response: dict[str, Any],
    waveform_session_id: str,
    routing_context: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    created = now_iso()
    return {
        "schemaVersion": "cardinal-escalation-case-v3",
        "eventId": event_id,
        "episodeId": episode_id,
        "incidentId": incident_id,
        "scenarioId": scenario_id,
        "waveformSessionId": waveform_session_id,
        "provider": provider,
        "patientId": patient_id,
        "encounterId": encounter_id,
        "modelSuggestedLevel": model_suggested_level.value,
        "policyMinimumLevel": policy_minimum_level.value,
        "effectiveLevel": effective_level.value,
        "effectiveLevelLabel": level_label(effective_level),
        "assignedRole": level_role(effective_level),
        "status": "CREATED",
        "modelRationale": model_rationale,
        "modelConfidence": model_confidence,
        "modelResponse": model_response,
        "createdAt": created,
        "updatedAt": created,
        "acknowledgedAt": None,
        "acknowledgedBy": None,
        "acknowledgedRole": None,
        "acknowledgementNote": None,
        "resolvedAt": None,
        "resolvedBy": None,
        "resolvedRole": None,
        "resolutionNote": None,
        "nextEscalationAt": None,
        "ackDueAt": None,
        "timeToAckSeconds": None,
        "idempotencyKey": idempotency_key,
        "idempotencyKeys": [idempotency_key],
        "delivery": {},
        "timeline": [],
        "levelHistory": [
            {
                "level": normalize_level(effective_level).value,
                "label": level_label(effective_level),
                "enteredAt": created,
                "reason": "initial_adjudication",
            }
        ],
        # Stored in the backend file only. No OAuth token is stored here.
        "routingContext": dict(routing_context),
    }
