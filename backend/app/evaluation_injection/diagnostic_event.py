from __future__ import annotations

from typing import Any

from app.evaluation_injection.evidence_normalizer import (
    normalize_scenario_evidence,
)


def build_authoritative_diagnostic_event(
    *,
    scenario_id: str,
    episode_id: str,
    incident_id: str,
    scenario_payload: dict[str, Any],
    capture_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Compatibility wrapper used by existing service and tests."""

    diagnostic_event, _ = normalize_scenario_evidence(
        scenario_id=scenario_id,
        episode_id=episode_id,
        incident_id=incident_id,
        scenario_payload=scenario_payload,
        capture_evidence=capture_evidence,
    )

    return diagnostic_event
