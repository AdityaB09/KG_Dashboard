from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path
from typing import Any

from app.config import settings
from app.epic_sandbox import EPIC_SANDBOX_PATIENTS


class EpicEvaluationMappingError(RuntimeError):
    pass


def _mapping_path() -> Path:
    configured = Path(settings.EPIC_EVALUATION_DEMO_MAP_PATH)
    if configured.is_absolute():
        return configured
    backend_root = Path(__file__).resolve().parents[2]
    return (backend_root / configured).resolve()


def load_epic_mapping() -> dict[str, Any]:
    path = _mapping_path()
    if not path.exists():
        raise EpicEvaluationMappingError(f"Epic evaluation mapping file was not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EpicEvaluationMappingError(f"Epic evaluation mapping is invalid: {error}") from error
    if not isinstance(payload, dict):
        raise EpicEvaluationMappingError("Epic evaluation mapping must contain a JSON object.")
    return payload


def _allowed_candidates(values: list[str]) -> list[str]:
    allowed = set(settings.EVALUATION_INJECTION_ALLOWED_SCENARIOS)
    return [value for value in values if value in allowed]


def _candidate_scenarios(plan: dict[str, Any], *, source: str) -> list[str]:
    configured = plan.get("scenarioIds")
    if configured is None:
        configured = [plan.get("scenarioId")]
    if not isinstance(configured, list):
        raise EpicEvaluationMappingError(f"Mapping {source} scenarioIds must be an array.")

    values: list[str] = []
    for raw in configured:
        value = str(raw or "").strip()
        if value and value not in values:
            values.append(value)

    eligible = _allowed_candidates(values)
    if not eligible:
        raise EpicEvaluationMappingError(
            f"Mapping {source} has no scenario in EVALUATION_INJECTION_ALLOWED_SCENARIOS."
        )
    return eligible


def _select(
    candidates: list[str],
    *,
    source: str,
    selection_key: str | None,
) -> tuple[str, str]:
    if len(candidates) == 1:
        return candidates[0], "single"
    if selection_key:
        # This hash is only for a stable 1-of-N choice among scenarios that are
        # explicitly assigned to a verified canonical Epic patient. It is NOT
        # an unknown-patient fallback and never maps an unknown Patient ID.
        digest = hashlib.sha256(f"epic-explicit|{source}|{selection_key}".encode()).digest()
        index = int.from_bytes(digest[:8], "big") % len(candidates)
        return candidates[index], "stable_random_per_smart_session"
    return secrets.choice(candidates), "random"


def _plan(
    plan: dict[str, Any],
    *,
    source: str,
    selection_key: str | None,
) -> dict[str, Any]:
    if plan.get("enabled") is False:
        raise EpicEvaluationMappingError(f"Automatic Epic evaluation is disabled for {source}.")

    candidates = _candidate_scenarios(plan, source=source)
    scenario, mode = _select(candidates, source=source, selection_key=selection_key)
    return {
        "scenarioId": scenario,
        "scenarioCandidates": candidates,
        "selectionMode": mode,
        "baselineSeconds": float(plan.get("baselineSeconds", 10.0)),
        "preSeconds": float(plan.get("preSeconds", 6.0)),
        "postSeconds": float(plan.get("postSeconds", 6.0)),
        "runSlm": bool(plan.get("runSlm", True)),
        "mappingSource": source,
        "mappingNote": str(plan.get("note") or "").strip() or None,
        "exactMapping": True,
        "scenarioSelectionSource": "explicit-config",
    }


def resolve_epic_patient_plan(
    *,
    patient_key: str,
    selection_key: str | None = None,
) -> dict[str, Any]:
    key = str(patient_key or "").strip()
    if key not in EPIC_SANDBOX_PATIENTS:
        raise EpicEvaluationMappingError(
            f"Epic sandbox patient key {key!r} is not recognized. No scenario was selected."
        )

    mapping = load_epic_mapping()
    by_key = mapping.get("patientsByKey") or {}
    if not isinstance(by_key, dict):
        raise EpicEvaluationMappingError("Epic mapping patientsByKey must be an object.")

    configured = by_key.get(key)
    if not isinstance(configured, dict):
        raise EpicEvaluationMappingError(
            f"Verified Epic sandbox patient {key!r} has no explicit scenario mapping in {_mapping_path()}."
        )

    return _plan(
        configured,
        source=f"epic-patient-key:{key}",
        selection_key=selection_key,
    )
