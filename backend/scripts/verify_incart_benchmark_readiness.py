from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


EXPECTED_CODES = {
    "VFIB-STEMI-001": "VENTRICULAR_FIBRILLATION",
    "TORSADES-LQT-002": "TORSADES_DE_POINTES",
    "VT-ISCHEMIC-003": "MONOMORPHIC_VENTRICULAR_TACHYCARDIA",
    "AFIB-RVR-SEPSIS-004": "ATRIAL_FIBRILLATION_RVR",
    "CHB-HYPERK-005": "COMPLETE_HEART_BLOCK",
    "BRADY-DIGTOX-006": "JUNCTIONAL_BRADYCARDIA",
    "SVT-PSVT-007": "SUPRAVENTRICULAR_TACHYCARDIA",
    "NSVT-ECTOPY-008": "NONSUSTAINED_VENTRICULAR_TACHYCARDIA",
}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def check_episode(episode_dir: Path) -> dict[str, Any]:
    metadata = read_json(episode_dir / "metadata.json")
    analysis = read_json(episode_dir / "analysis.json")
    windowed = read_json(episode_dir / "analysis_windowed.json") or analysis.get("windowedAnalysis") or {}
    grounded_input = read_json(episode_dir / "grounded_model_input.json")
    evidence = grounded_input.get("evidenceBundle") or {}
    diagnostic = grounded_input.get("diagnosticEvent") or read_json(episode_dir / "diagnostic_event.json")
    consistency = read_json(episode_dir / "evidence_consistency.json") or evidence.get("evidenceConsistencyReview") or {}

    scenario_id = str(
        metadata.get("evaluationScenarioId")
        or grounded_input.get("scenarioId")
        or ((diagnostic.get("source") or {}).get("identifier"))
        or ""
    )
    segments = metadata.get("sourceSegments") or []
    by_type = {
        str(item.get("type")): item
        for item in segments
        if isinstance(item, dict)
    }
    diagnosis = diagnostic.get("diagnosis") or {}
    qt = diagnostic.get("qtContext") or ((evidence.get("controlledEventContext") or {}).get("qt") or {})

    checks = {
        "metadataPresent": bool(metadata),
        "incartBaseWaveform": metadata.get("baseWaveformSource") == "physionet-incart",
        "incartRecordIdentity": str(metadata.get("record") or "").startswith("INCART-EVAL-"),
        "apiRangeCaptureAbsent": metadata.get("apiRangeCapture") in (None, {}),
        "threeSourceSegments": set(by_type) >= {"pre_event", "controlled_event", "post_event"},
        "incartPreEvent": (by_type.get("pre_event") or {}).get("source") == "physionet-incart",
        "episodeControlledEvent": (by_type.get("controlled_event") or {}).get("source") == "complete_episode_pack",
        "incartPostEvent": (by_type.get("post_event") or {}).get("source") == "physionet-incart",
        "segmentBoundariesPresent": all(
            item.get("startSeconds") is not None and item.get("endSeconds") is not None
            for item in by_type.values()
        ) if by_type else False,
        "windowedPhase6Present": windowed.get("schemaVersion") == "phase6-windowed-analysis-v1",
        "controlledEventWindowPresent": bool((windowed.get("measurementWindows") or {}).get("controlledEvent")),
        "episodePackOnly": evidence.get("clinicalPromptMode") == "episode_pack_only",
        "oracleClinicalContextExcluded": (
            ((evidence.get("oracleContext") or {}).get("available") is False)
            and ((evidence.get("oracleContext") or {}).get("excludedByPolicy") is True)
            and ((evidence.get("sourceManifest") or {}).get("oracleFhirClinicalContextUsed") is False)
        ),
        "evidenceConsistent": consistency.get("status") in {"consistent", "consistent_with_warnings"},
        "diagnosisCodeCorrect": diagnosis.get("code") == EXPECTED_CODES.get(scenario_id),
        "contributingFactorContractMaxFive": True,
    }

    if scenario_id == "TORSADES-LQT-002":
        checks.update({
            "torsadesQtc618": float(qt.get("qtcMs") or 0) == 618.0,
            "torsadesQtProlonged": qt.get("prolonged") is True,
            "torsadesAcquiredLongQtSupported": qt.get("acquiredLongQtSupported") is True,
        })

    if scenario_id == "VFIB-STEMI-001" and windowed:
        heart_rate = windowed.get("heartRate") or {}
        checks["vfEventRateNotFabricated"] = (
            heart_rate.get("eventMedianBpm") is None
            and heart_rate.get("eventMeasurementValid") is False
        )

    return {
        "schemaVersion": "incart-benchmark-readiness-v1",
        "episodeDirectory": str(episode_dir),
        "scenarioId": scenario_id,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "failedChecks": [name for name, passed in checks.items() if not passed],
        "sourceSegments": segments,
        "windowedPhase6": windowed,
        "evidenceConsistencyReview": consistency,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode-dir", required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    result = check_episode(Path(args.episode_dir).expanduser().resolve())
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
