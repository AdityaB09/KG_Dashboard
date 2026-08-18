from __future__ import annotations

from typing import Any

from app.escalation.repository import escalation_repository


def find_active_epic_escalation(
    *,
    patient_id: str,
    encounter_id: str | None = None,
) -> dict[str, Any] | None:
    return escalation_repository.find_active(
        patient_id=patient_id,
        encounter_id=encounter_id,
        provider="epic",
    )
