from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.config import settings


class OracleEvaluationMappingError(RuntimeError):
    pass


def _normalized_name(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _mapping_path() -> Path:
    configured = Path(settings.ORACLE_EVALUATION_DEMO_MAP_PATH)
    if configured.is_absolute():
        return configured

    backend_root = Path(__file__).resolve().parents[2]
    return (backend_root / configured).resolve()


def load_mapping() -> dict[str, Any]:
    path = _mapping_path()
    if not path.exists():
        raise OracleEvaluationMappingError(
            f"Oracle evaluation mapping file was not found: {path}"
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OracleEvaluationMappingError(
            f"Oracle evaluation mapping is invalid: {error}"
        ) from error

    if not isinstance(payload, dict):
        raise OracleEvaluationMappingError(
            "Oracle evaluation mapping must contain a JSON object."
        )

    return payload


def _validate_plan(plan: dict[str, Any], *, source: str) -> dict[str, Any]:
    if plan.get("enabled") is False:
        raise OracleEvaluationMappingError(
            f"Automatic evaluation is disabled for mapping {source}."
        )

    scenario_id = str(plan.get("scenarioId") or "").strip()
    if not scenario_id:
        raise OracleEvaluationMappingError(
            f"Mapping {source} does not define scenarioId."
        )

    if scenario_id not in settings.EVALUATION_INJECTION_ALLOWED_SCENARIOS:
        raise OracleEvaluationMappingError(
            f"Scenario {scenario_id} is not in "
            "EVALUATION_INJECTION_ALLOWED_SCENARIOS."
        )

    return {
        "scenarioId": scenario_id,
        "baselineSeconds": float(plan.get("baselineSeconds", 10.0)),
        "preSeconds": float(plan.get("preSeconds", 6.0)),
        "postSeconds": float(plan.get("postSeconds", 6.0)),
        "runSlm": bool(plan.get("runSlm", True)),
        "mappingSource": source,
        "mappingNote": str(plan.get("note") or "").strip() or None,
    }


def resolve_patient_plan(
    *,
    patient_id: str,
    patient_display: str | None,
) -> dict[str, Any]:
    mapping = load_mapping()

    by_id = mapping.get("patientsById") or {}
    if isinstance(by_id, dict):
        exact = by_id.get(patient_id)
        if isinstance(exact, dict):
            return _validate_plan(exact, source=f"patient-id:{patient_id}")

    by_display = mapping.get("patientsByDisplay") or {}
    if isinstance(by_display, dict) and patient_display:
        target = _normalized_name(patient_display)
        for configured_name, value in by_display.items():
            if _normalized_name(configured_name) == target and isinstance(value, dict):
                return _validate_plan(
                    value,
                    source=f"patient-display:{configured_name}",
                )

    if settings.ORACLE_EVALUATION_DEMO_ALLOW_DEFAULT_SCENARIO:
        default_plan = mapping.get("defaultPlan")
        if isinstance(default_plan, dict):
            return _validate_plan(default_plan, source="defaultPlan")

    raise OracleEvaluationMappingError(
        "The authenticated Oracle patient is not mapped to an evaluation "
        f"scenario. patientId={patient_id!r}, patientDisplay={patient_display!r}. "
        "Add the Oracle Patient resource ID to patientsById in "
        f"{_mapping_path()}."
    )
