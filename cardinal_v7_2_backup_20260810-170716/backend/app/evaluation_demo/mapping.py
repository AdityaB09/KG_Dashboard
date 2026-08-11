from __future__ import annotations

import hashlib
import json
import re
import secrets
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


def _candidate_scenarios(plan: dict[str, Any], *, source: str) -> list[str]:
    configured = plan.get("scenarioIds")
    if configured is None:
        configured = [plan.get("scenarioId")]

    if not isinstance(configured, list):
        raise OracleEvaluationMappingError(
            f"Mapping {source} scenarioIds must be a JSON array."
        )

    candidates: list[str] = []
    for item in configured:
        scenario_id = str(item or "").strip()
        if scenario_id and scenario_id not in candidates:
            candidates.append(scenario_id)

    if not candidates:
        raise OracleEvaluationMappingError(
            f"Mapping {source} does not define scenarioId or scenarioIds."
        )

    allowed = set(settings.EVALUATION_INJECTION_ALLOWED_SCENARIOS)
    eligible = [scenario_id for scenario_id in candidates if scenario_id in allowed]
    if not eligible:
        raise OracleEvaluationMappingError(
            "None of the mapped scenarios are in "
            "EVALUATION_INJECTION_ALLOWED_SCENARIOS. configured="
            + ", ".join(candidates)
        )

    # Backward-compatible rollout: an existing deployment that still has the
    # legacy 8-scenario env value keeps working. Once the env value is expanded
    # to all 14 scenarios, the additional candidates automatically participate.
    return eligible


def _select_scenario(
    candidates: list[str],
    *,
    source: str,
    selection_key: str | None,
) -> tuple[str, str]:
    if len(candidates) == 1:
        return candidates[0], "single"

    # A SMART authorization already has a random session key. Hashing that key
    # gives us a random choice across authorizations while keeping bootstrap,
    # start, React remounts and browser refreshes stable for the same session.
    if selection_key:
        digest = hashlib.sha256(
            f"{source}|{selection_key}".encode("utf-8")
        ).digest()
        index = int.from_bytes(digest[:8], "big") % len(candidates)
        return candidates[index], "stable_random_per_smart_session"

    return secrets.choice(candidates), "random"


def _validate_plan(
    plan: dict[str, Any],
    *,
    source: str,
    selection_key: str | None = None,
) -> dict[str, Any]:
    if plan.get("enabled") is False:
        raise OracleEvaluationMappingError(
            f"Automatic evaluation is disabled for mapping {source}."
        )

    candidates = _candidate_scenarios(plan, source=source)
    scenario_id, selection_mode = _select_scenario(
        candidates,
        source=source,
        selection_key=selection_key,
    )

    return {
        "scenarioId": scenario_id,
        "scenarioCandidates": candidates,
        "selectionMode": selection_mode,
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
    selection_key: str | None = None,
) -> dict[str, Any]:
    mapping = load_mapping()

    by_id = mapping.get("patientsById") or {}
    if isinstance(by_id, dict):
        exact = by_id.get(patient_id)
        if isinstance(exact, dict):
            return _validate_plan(
                exact,
                source=f"patient-id:{patient_id}",
                selection_key=selection_key,
            )

    by_display = mapping.get("patientsByDisplay") or {}
    if isinstance(by_display, dict) and patient_display:
        target = _normalized_name(patient_display)
        for configured_name, value in by_display.items():
            if _normalized_name(configured_name) == target and isinstance(value, dict):
                return _validate_plan(
                    value,
                    source=f"patient-display:{configured_name}",
                    selection_key=selection_key,
                )

    if settings.ORACLE_EVALUATION_DEMO_ALLOW_DEFAULT_SCENARIO:
        default_plan = mapping.get("defaultPlan")
        if isinstance(default_plan, dict):
            return _validate_plan(
                default_plan,
                source="defaultPlan",
                selection_key=selection_key,
            )

    raise OracleEvaluationMappingError(
        "The authenticated Oracle patient is not mapped to an evaluation "
        f"scenario. patientId={patient_id!r}, patientDisplay={patient_display!r}. "
        "Add the Oracle Patient resource ID to patientsById in "
        f"{_mapping_path()}."
    )
