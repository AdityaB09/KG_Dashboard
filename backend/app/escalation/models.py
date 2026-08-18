from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.config import settings
from app.escalation.levels import EscalationLevel, level_label, level_role, normalize_level, reference_band, tier_code


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_event_id() -> str:
    return f"esc-{uuid4().hex[:20]}"


def new_correlation_id() -> str:
    return f"corr-{uuid4().hex}"


def _canonicalize_level_field(result: dict[str, Any], key: str) -> None:
    if not result.get(key):
        return
    try:
        result[key] = normalize_level(result[key]).value
    except ValueError:
        pass


def public_case(case: dict[str, Any]) -> dict[str, Any]:
    """Remove internal routing refs and present legacy cases using V10 pathways."""
    result = dict(case)
    result.pop("routingContext", None)
    for key in ("modelSuggestedLevel", "policyMinimumLevel", "effectiveLevel", "acknowledgedLevel"):
        _canonicalize_level_field(result, key)
    for source_key, label_key in (
        ("modelSuggestedLevel", "modelSuggestedLevelLabel"),
        ("policyMinimumLevel", "policyMinimumLevelLabel"),
        ("effectiveLevel", "effectiveLevelLabel"),
    ):
        if not result.get(source_key):
            continue
        try:
            result[label_key] = level_label(normalize_level(result[source_key]))
        except ValueError:
            pass
    if result.get("effectiveLevel"):
        try:
            level = normalize_level(result["effectiveLevel"])
            result["assignedRole"] = level_role(level)
            result["responseTierCode"] = tier_code(level)
            result["referenceSeverityBand"] = reference_band(level)
        except ValueError:
            pass
    # V10 deliberately removes manual ACK/resolution from the user workflow.
    result.setdefault("autoEscalationEnabled", False)
    result.setdefault("nextEscalationAt", None)
    result["responsePathwayVersion"] = "oracle-millennium-hospital-response-v1"
    result["responseTierProfile"] = "cardinal-cerner-grounded-t0-t3-e-v1"
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
        "schemaVersion": "cardinal-response-case-v4",
        "responsePathwayVersion": "oracle-millennium-hospital-response-v1",
        "responseTierProfile": "cardinal-cerner-grounded-t0-t3-e-v1",
        "eventId": event_id,
        "episodeId": episode_id,
        "incidentId": incident_id,
        "scenarioId": scenario_id,
        "waveformSessionId": waveform_session_id,
        "provider": provider,
        "patientId": patient_id,
        "encounterId": encounter_id,
        "modelSuggestedLevel": normalize_level(model_suggested_level).value,
        "modelSuggestedLevelLabel": level_label(model_suggested_level),
        "policyMinimumLevel": normalize_level(policy_minimum_level).value,
        "policyMinimumLevelLabel": level_label(policy_minimum_level),
        "effectiveLevel": normalize_level(effective_level).value,
        "effectiveLevelLabel": level_label(effective_level),
        "responseTierCode": tier_code(effective_level),
        "referenceSeverityBand": reference_band(effective_level),
        "assignedRole": level_role(effective_level),
        "status": "CREATED",
        "modelRationale": model_rationale,
        "modelConfidence": model_confidence,
        "modelResponse": model_response,
        "createdAt": created,
        "updatedAt": created,
        "autoEscalationEnabled": bool(settings.ESCALATION_AUTO_ADVANCE_DEFAULT),
        "nextEscalationAt": None,
        "idempotencyKey": idempotency_key,
        "idempotencyKeys": [idempotency_key],
        "delivery": {},
        "timeline": [],
        "levelHistory": [
            {
                "level": normalize_level(effective_level).value,
                "label": level_label(effective_level),
                "tierCode": tier_code(effective_level),
                "enteredAt": created,
                "reason": "initial_adjudication",
            }
        ],
        # Stored in the backend case file only. OAuth tokens are never stored here.
        "routingContext": dict(routing_context),
    }
