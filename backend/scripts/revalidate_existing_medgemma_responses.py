from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.evaluation_injection.answer_key_loader import load_scenario_answer_key
from app.evaluation_injection.evidence_consistency import (
    apply_evidence_consistency_preflight,
    evidence_invalid_validation,
)
from app.evaluation_injection.etiology_context_scorer import score_etiology_context_response
from app.evaluation_injection.response_validator import validate_grounded_response


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def five_field_response(record: dict[str, Any]) -> dict[str, Any]:
    for key in (
        "normalizedGroundedModelResponse",
        "rawGroundedModelResponse",
        "modelResponse",
        "response",
    ):
        candidate = record.get(key)
        if not isinstance(candidate, dict):
            continue
        if "detectedEpisodeContext" in candidate:
            return candidate
        if "clinicalContext" in candidate:
            return {
                "episodeSummary": candidate.get("episodeSummary") or "",
                "detectedEpisodeContext": candidate.get("clinicalContext") or "",
                "mostLikelyEtiology": candidate.get("mostLikelyEtiology") or "",
                "contributingFactors": candidate.get("contributingFactors") or [],
                "uncertaintyAndMissingData": candidate.get("uncertaintyAndMissingData") or [],
            }
    return {}


def revalidate(run_dir: Path, output_root: Path) -> dict[str, Any]:
    input_record = read_json(run_dir / "grounded_model_input.json")
    response_record = read_json(run_dir / "cardinal_model_response.json")
    if not input_record or not response_record:
        return {
            "status": "skipped",
            "runDirectory": str(run_dir),
            "reason": "grounded_model_input.json or cardinal_model_response.json missing",
        }

    scenario_id = str(input_record.get("scenarioId") or "")
    episode_id = str(input_record.get("episodeId") or run_dir.name)
    diagnostic = input_record.get("diagnosticEvent") or {}
    evidence = input_record.get("evidenceBundle") or {}
    response = five_field_response(response_record)

    diagnostic, evidence, consistency = apply_evidence_consistency_preflight(
        diagnostic_event=diagnostic,
        evidence_bundle=evidence,
    )

    if consistency.get("status") == "evidence_invalid":
        validation = evidence_invalid_validation(consistency, diagnostic_event=diagnostic)
        score = {
            "grounding": {"status": "evidence_invalid", "pass": False},
            "benchmark": {"score": None, "pass": None, "grade": "not_scored"},
            "safetyPass": False,
            "overallPass": False,
            "overallDisposition": "evidence_invalid_before_generation",
        }
    elif not response:
        validation = {
            "status": "generation_failed",
            "groundingStatus": "generation_failed",
            "accepted": False,
            "displayableWithReview": False,
            "hardErrors": ["No five-field model response was available."],
            "qualityErrors": [],
            "contradictions": [],
            "unsupportedFacts": [],
        }
        score = {
            "grounding": {"status": "generation_failed", "pass": False},
            "benchmark": {"score": None, "pass": None, "grade": "not_scored"},
            "safetyPass": False,
            "overallPass": False,
            "overallDisposition": "generation_failed",
        }
    else:
        validation = validate_grounded_response(
            response=response,
            diagnostic_event=diagnostic,
            supplied_evidence=evidence,
        )
        answer_key = load_scenario_answer_key(scenario_id)
        score = score_etiology_context_response(
            episode_id=scenario_id,
            model_response=response,
            diagnostic_event=diagnostic,
            validation=validation,
            answer_key=answer_key,
            benchmark_alignment_mode=str(evidence.get("benchmarkAlignmentMode") or "full_scenario"),
            clinical_prompt_mode=str(evidence.get("clinicalPromptMode") or "episode_pack_only"),
            scoped_evidence=evidence,
        )

    result = {
        "schemaVersion": "imported-response-revalidation-v6",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "runDirectory": str(run_dir),
        "scenarioId": scenario_id,
        "episodeId": episode_id,
        "sourceResponsePreserved": True,
        "evidenceConsistencyReview": consistency,
        "validation": validation,
        "score": score,
    }
    destination = output_root / scenario_id / run_dir.name / "revalidation_v6.json"
    atomic_json(destination, result)
    result["outputFile"] = str(destination)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Revalidate saved MedGemma/model responses without regenerating them."
    )
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()

    input_root = Path(args.input_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    run_dirs = sorted({path.parent for path in input_root.rglob("grounded_model_input.json")})
    results = [revalidate(run_dir, output_root) for run_dir in run_dirs]

    report = {
        "schemaVersion": "imported-response-revalidation-summary-v6",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "runCount": len(results),
        "evidenceInvalidCount": sum(
            1 for item in results
            if ((item.get("validation") or {}).get("groundingStatus") == "evidence_invalid")
        ),
        "strictAcceptedCount": sum(
            1 for item in results if ((item.get("validation") or {}).get("accepted") is True)
        ),
        "acceptedWithReviewCount": sum(
            1 for item in results
            if ((item.get("validation") or {}).get("displayableWithReview") is True)
        ),
        "safetyPassCount": sum(
            1 for item in results if ((item.get("score") or {}).get("safetyPass") is True)
        ),
        "benchmarkPassCount": sum(
            1 for item in results
            if (((item.get("score") or {}).get("benchmark") or {}).get("pass") is True)
        ),
        "runs": results,
    }
    atomic_json(output_root / "revalidation_summary_v6.json", report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
