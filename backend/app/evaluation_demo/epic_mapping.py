from __future__ import annotations

import hashlib
import json
import re
import secrets
from pathlib import Path
from typing import Any

from app.config import settings


class EpicEvaluationMappingError(RuntimeError):
    pass


def _normalized_name(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


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
    return [v for v in values if v in allowed]


def _candidate_scenarios(plan: dict[str, Any], *, source: str) -> list[str]:
    configured = plan.get("scenarioIds")
    if configured is None:
        configured = [plan.get("scenarioId")]
    if not isinstance(configured, list):
        raise EpicEvaluationMappingError(f"Mapping {source} scenarioIds must be an array.")
    values=[]
    for raw in configured:
        value=str(raw or "").strip()
        if value and value not in values:
            values.append(value)
    eligible=_allowed_candidates(values)
    if not eligible:
        raise EpicEvaluationMappingError(f"Mapping {source} has no scenario in EVALUATION_INJECTION_ALLOWED_SCENARIOS.")
    return eligible


def _select(candidates: list[str], *, source: str, selection_key: str | None) -> tuple[str, str]:
    if len(candidates)==1:
        return candidates[0], "single"
    if selection_key:
        digest=hashlib.sha256(f"epic|{source}|{selection_key}".encode()).digest()
        return candidates[int.from_bytes(digest[:8],"big") % len(candidates)], "stable_random_per_smart_session"
    return secrets.choice(candidates), "random"


def _plan(plan: dict[str, Any], *, source: str, selection_key: str | None) -> dict[str, Any]:
    if plan.get("enabled") is False:
        raise EpicEvaluationMappingError(f"Automatic Epic evaluation is disabled for {source}.")
    candidates=_candidate_scenarios(plan, source=source)
    scenario,mode=_select(candidates, source=source, selection_key=selection_key)
    return {
        "scenarioId": scenario,
        "scenarioCandidates": candidates,
        "selectionMode": mode,
        "baselineSeconds": float(plan.get("baselineSeconds",10.0)),
        "preSeconds": float(plan.get("preSeconds",6.0)),
        "postSeconds": float(plan.get("postSeconds",6.0)),
        "runSlm": bool(plan.get("runSlm",True)),
        "mappingSource": source,
        "mappingNote": str(plan.get("note") or "").strip() or None,
        "exactMapping": True,
    }


def _hash_fallback(mapping: dict[str, Any], patient_id: str) -> dict[str, Any]:
    fallback=mapping.get("catalogFallback") or {}
    configured=fallback.get("scenarioIds") or []
    candidates=_allowed_candidates([str(v).strip() for v in configured if str(v).strip()])
    if not candidates:
        raise EpicEvaluationMappingError("Epic hash fallback is enabled but catalogFallback.scenarioIds is empty.")
    digest=hashlib.sha256(f"epic-patient|{patient_id}".encode()).digest()
    scenario=candidates[int.from_bytes(digest[:8],"big") % len(candidates)]
    return {
        "scenarioId": scenario,
        "scenarioCandidates": [scenario],
        "selectionMode": "stable_hash_by_epic_patient",
        "baselineSeconds": float(fallback.get("baselineSeconds",10.0)),
        "preSeconds": float(fallback.get("preSeconds",6.0)),
        "postSeconds": float(fallback.get("postSeconds",6.0)),
        "runSlm": bool(fallback.get("runSlm",True)),
        "mappingSource": f"epic-hash-fallback:{patient_id}",
        "mappingNote": "Sandbox fallback used because this Epic FHIR Patient ID has not yet been added to the exact Epic mapping file.",
        "exactMapping": False,
    }


def resolve_epic_patient_plan(*, patient_id: str, patient_display: str | None = None, selection_key: str | None = None) -> dict[str, Any]:
    mapping=load_epic_mapping()
    by_id=mapping.get("patientsById") or {}
    exact=by_id.get(patient_id) if isinstance(by_id,dict) else None
    if isinstance(exact,dict):
        return _plan(exact, source=f"epic-patient-id:{patient_id}", selection_key=selection_key)

    by_display=mapping.get("patientsByDisplay") or {}
    if isinstance(by_display,dict) and patient_display:
        target=_normalized_name(patient_display)
        for name,value in by_display.items():
            if _normalized_name(name)==target and isinstance(value,dict):
                return _plan(value, source=f"epic-patient-display:{name}", selection_key=selection_key)

    if settings.EPIC_EVALUATION_DEMO_ALLOW_HASH_FALLBACK:
        return _hash_fallback(mapping, patient_id)

    raise EpicEvaluationMappingError(
        "The authenticated Epic patient is not mapped. Add the exact Patient FHIR ID "
        f"{patient_id!r} to patientsById in {_mapping_path()}."
    )
