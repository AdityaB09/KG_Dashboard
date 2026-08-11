from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


SCENARIO_ORDER: tuple[str, ...] = (
    "VFIB-STEMI-001",
    "TORSADES-LQT-002",
    "VT-ISCHEMIC-003",
    "AFIB-RVR-SEPSIS-004",
    "CHB-HYPERK-005",
    "BRADY-DIGTOX-006",
    "SVT-PSVT-007",
    "NSVT-ECTOPY-008",
)

SCENARIO_FALLBACKS: dict[str, dict[str, Any]] = {
    "VFIB-STEMI-001": {
        "display": "Ventricular fibrillation",
        "shortLabel": "Ventricular fibrillation",
        "symbol": "VF",
        "category": "ventricular_fibrillation",
    },
    "TORSADES-LQT-002": {
        "display": "Torsades de pointes",
        "shortLabel": "Torsades de pointes",
        "symbol": "TdP",
        "category": "polymorphic_ventricular_tachycardia",
    },
    "VT-ISCHEMIC-003": {
        "display": "Monomorphic ventricular tachycardia",
        "shortLabel": "Monomorphic VT",
        "symbol": "VT",
        "category": "ventricular_tachycardia",
    },
    "AFIB-RVR-SEPSIS-004": {
        "display": "Atrial fibrillation with rapid ventricular response",
        "shortLabel": "AF with RVR",
        "symbol": "AF",
        "category": "atrial_fibrillation_rvr",
    },
    "CHB-HYPERK-005": {
        "display": "Complete heart block",
        "shortLabel": "Complete heart block",
        "symbol": "CHB",
        "category": "complete_heart_block",
    },
    "BRADY-DIGTOX-006": {
        "display": "Symptomatic bradyarrhythmia",
        "shortLabel": "Bradyarrhythmia",
        "symbol": "BRADY",
        "category": "bradyarrhythmia",
    },
    "SVT-PSVT-007": {
        "display": "Paroxysmal supraventricular tachycardia",
        "shortLabel": "PSVT",
        "symbol": "SVT",
        "category": "supraventricular_tachycardia",
    },
    "NSVT-ECTOPY-008": {
        "display": "Nonsustained ventricular tachycardia with ectopy",
        "shortLabel": "NSVT with ectopy",
        "symbol": "NSVT",
        "category": "nonsustained_ventricular_tachycardia",
    },
}

# The current project has a dedicated sustained VT detector. The other
# scenarios use a diagnosis-neutral waveform-change gate with a timed
# capture fallback. This permits one real capture per scenario without
# claiming that the gate independently diagnosed the rhythm.
DETECTOR_POLICY: dict[str, dict[str, Any]] = {
    scenario_id: {
        "mode": (
            "existing_vt_detector"
            if scenario_id == "VT-ISCHEMIC-003"
            else "waveform_change_with_capture_fallback"
        ),
        "holdSeconds": 1.2,
        "fallbackSeconds": 1.5,
        "isIndependentDiagnosis": False,
    }
    for scenario_id in SCENARIO_ORDER
}


def default_allowed_scenarios() -> list[str]:
    return list(SCENARIO_ORDER)


def _safe_episode_record(dataset_root: Path, scenario_id: str) -> dict[str, Any] | None:
    path = dataset_root / "episodes" / f"{scenario_id}.json"

    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    return payload if isinstance(payload, dict) else None


def scenario_descriptor(
    scenario_id: str,
    *,
    dataset_root: Path | None = None,
) -> dict[str, Any]:
    fallback = SCENARIO_FALLBACKS.get(
        scenario_id,
        {
            "display": scenario_id,
            "shortLabel": scenario_id,
            "symbol": "EVENT",
            "category": "evaluation_event",
        },
    )
    record = _safe_episode_record(dataset_root, scenario_id) if dataset_root else None
    episode = (record or {}).get("episode") or {}

    display = str(episode.get("display") or fallback["display"])
    severity = str(episode.get("severity") or "warning")
    duration = episode.get("durationSeconds")
    policy = DETECTOR_POLICY.get(
        scenario_id,
        {
            "mode": "waveform_change_with_capture_fallback",
            "holdSeconds": 1.2,
            "fallbackSeconds": 1.5,
            "isIndependentDiagnosis": False,
        },
    )

    return {
        "scenarioId": scenario_id,
        "display": display,
        "shortLabel": str(fallback["shortLabel"]),
        "severity": severity,
        "durationSeconds": duration,
        "symbol": str(fallback["symbol"]),
        "category": str(fallback["category"]),
        "triggerPolicy": dict(policy),
        "available": record is not None if dataset_root else True,
    }


def list_scenario_descriptors(
    allowed_scenarios: Iterable[str],
    *,
    dataset_root: Path | None = None,
) -> list[dict[str, Any]]:
    allowed = {str(item).strip() for item in allowed_scenarios if str(item).strip()}
    ordered = [scenario_id for scenario_id in SCENARIO_ORDER if scenario_id in allowed]
    ordered.extend(sorted(allowed.difference(ordered)))

    return [
        scenario_descriptor(scenario_id, dataset_root=dataset_root)
        for scenario_id in ordered
    ]


def detector_policy(scenario_id: str) -> dict[str, Any]:
    return dict(
        DETECTOR_POLICY.get(
            scenario_id,
            {
                "mode": "waveform_change_with_capture_fallback",
                "holdSeconds": 1.2,
                "fallbackSeconds": 1.5,
                "isIndependentDiagnosis": False,
            },
        )
    )


def detected_annotation_details(
    scenario_id: str,
    *,
    detector_rule_id: str | None,
) -> dict[str, Any]:
    descriptor = scenario_descriptor(scenario_id)
    policy = detector_policy(scenario_id)
    controlled_gate = policy["mode"] != "existing_vt_detector"

    return {
        "symbol": descriptor["symbol"],
        "category": descriptor["category"],
        "label": (
            "Controlled capture trigger"
            if controlled_gate
            else "Automatic VT trigger"
        ),
        "display": (
            f"Controlled capture trigger for {descriptor['display']}"
            if controlled_gate
            else "Automatic sustained multi-lead tachycardia trigger"
        ),
        "source": (
            "evaluation_waveform_change_gate"
            if controlled_gate
            else "evaluation_injection_detector"
        ),
        "triggerMode": policy["mode"],
        "ruleId": detector_rule_id,
        "isIndependentDiagnosis": False,
    }
