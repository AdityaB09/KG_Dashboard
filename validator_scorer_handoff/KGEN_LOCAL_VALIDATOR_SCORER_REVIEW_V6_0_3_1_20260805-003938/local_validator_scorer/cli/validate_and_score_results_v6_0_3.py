from __future__ import annotations

import argparse
import csv
import json
import tempfile
import zipfile
import sys
import types
from collections import defaultdict
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def extract(zip_path: Path, target: Path) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(target)


def find_results(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    output: list[tuple[Path, dict[str, Any]]] = []
    for path in root.rglob("*.json"):
        try:
            payload = load(path)
        except Exception:
            continue
        if (
            payload.get("scenarioId")
            and (payload.get("modelResponse") or payload.get("parsedResponse"))
        ):
            output.append((path, payload))
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-root", required=True)
    parser.add_argument("--input-zip", required=True)
    parser.add_argument("--results-zip", required=True)
    parser.add_argument("--output-directory", required=True)
    args = parser.parse_args()

    backend_root = Path(args.backend_root).resolve()
    if not (backend_root / "app").is_dir():
        raise FileNotFoundError(
            f"Backend root must contain app/: {backend_root}"
        )

    sys.path.insert(0, str(backend_root))

    # Avoid importing app.evaluation_injection.__init__, which eagerly loads
    # the full waveform service stack. The validator/scorer CLI needs only
    # the source modules below and should run with validator-only dependencies.
    import app
    package_name = "app.evaluation_injection"
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(backend_root / "app" / "evaluation_injection")]
        package.__package__ = package_name
        sys.modules[package_name] = package

    from app.evaluation_injection.answer_key_loader import load_scenario_answer_key
    from app.evaluation_injection.compatibility_adapter import (
        adapt_v6_0_3_to_legacy_validator,
    )
    from app.evaluation_injection.etiology_context_scorer import (
        score_etiology_context_response,
    )
    from app.evaluation_injection.response_validator import validate_grounded_response

    output_root = Path(args.output_directory).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as input_temp, tempfile.TemporaryDirectory() as result_temp:
        input_root = Path(input_temp)
        result_root = Path(result_temp)
        extract(Path(args.input_zip).resolve(), input_root)
        extract(Path(args.results_zip).resolve(), result_root)

        manifest = load(input_root / "manifest.json")
        metadata = {
            item["scenarioId"]: item
            for item in manifest.get("items") or []
            if isinstance(item, dict) and item.get("scenarioId")
        }

        for path, result in find_results(result_root):
            scenario = str(result["scenarioId"])
            item = metadata.get(scenario)
            if not item:
                continue

            expected_fingerprint = str(item.get("promptFingerprint") or "")
            result_fingerprint = str(result.get("promptFingerprint") or "")
            if expected_fingerprint and result_fingerprint != expected_fingerprint:
                raise RuntimeError(
                    f"Prompt fingerprint mismatch for {scenario}: "
                    f"{result_fingerprint!r} != {expected_fingerprint!r}"
                )

            model_response = result.get("modelResponse") or result.get("parsedResponse")
            legacy = adapt_v6_0_3_to_legacy_validator(model_response)

            item_root = input_root / "items" / scenario
            diagnostic = load(item_root / "diagnostic_event.json")
            evidence = load(item_root / "validator_evidence.json")

            validation = validate_grounded_response(
                response=legacy,
                diagnostic_event=diagnostic,
                supplied_evidence=evidence,
            )

            # Mandatory real scenario answer key. Never use an empty fallback.
            answer_key = load_scenario_answer_key(
                scenario,
                allow_legacy_fallback=False,
            )

            score = score_etiology_context_response(
                episode_id=scenario,
                model_response=legacy,
                diagnostic_event=diagnostic,
                validation=validation,
                answer_key=answer_key,
            )

            row = {
                "model": result.get("model"),
                "scenarioId": scenario,
                "validContract": bool(result.get("validContract")),
                "generationAttempts": result.get("generationAttempts"),
                "contractNormalized": result.get("contractNormalized"),
                "latencySeconds": result.get("latencySeconds"),
                "inputTokens": result.get("inputTokens"),
                "outputTokens": result.get("outputTokens"),
                "peakGpuMemoryGiB": result.get("peakGpuMemoryGiB"),
                "accepted": bool(validation.get("accepted")),
                "validatorPassed": bool(
                    validation.get("validatorPassed", validation.get("accepted"))
                ),
                "displayableWithReview": bool(validation.get("displayableWithReview")),
                "hardErrorCount": len(validation.get("hardErrors") or []),
                "qualityErrorCount": len(validation.get("qualityErrors") or []),
                "unsupportedFactCount": len(validation.get("unsupportedFacts") or []),
                "contradictionCount": len(validation.get("contradictions") or []),
                "safetyPass": bool(score.get("safetyPass")),
                "overallPass": bool(score.get("overallPass")),
                "total": score.get("total"),
                "grade": score.get("grade"),
                "diagnosisConsistency": score.get("diagnosisConsistency"),
                "primaryEtiology": score.get("primaryEtiology"),
                "contextualEvidence": score.get("contextualEvidence"),
                "uncertainty": score.get("uncertaintyAndMissingData"),
                "avoidsDistractors": score.get("avoidsDistractors"),
                "answerKeySource": answer_key.get("source"),
                "file": str(path),
            }
            rows.append(row)
            details.append(
                {
                    **row,
                    "validation": validation,
                    "score": score,
                    "modelResponse": model_response,
                    "legacyResponse": legacy,
                    "answerKey": answer_key,
                }
            )

    rows.sort(key=lambda item: (str(item.get("model")), str(item.get("scenarioId"))))
    details.sort(key=lambda item: (str(item.get("model")), str(item.get("scenarioId"))))

    (output_root / "local_validation_report.json").write_text(
        json.dumps(details, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    with (output_root / "local_validation_report.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        fieldnames = list(rows[0]) if rows else ["model", "scenarioId"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("model"))].append(row)

    scorecard: list[dict[str, Any]] = []
    for model, model_rows in sorted(grouped.items()):
        scorecard.append(
            {
                "model": model,
                "scenarioCount": len(model_rows),
                "acceptedCount": sum(bool(item["accepted"]) for item in model_rows),
                "validatorPassedCount": sum(
                    bool(item["validatorPassed"]) for item in model_rows
                ),
                "averageScore": round(
                    sum(float(item.get("total") or 0) for item in model_rows)
                    / len(model_rows),
                    2,
                ),
            }
        )

    (output_root / "model_score_details.json").write_text(
        json.dumps(details, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    with (output_root / "model_scorecard.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        fieldnames = list(scorecard[0]) if scorecard else ["model"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(scorecard)

    audit = ["# KGEN V6.0.3.1 scenario audit", ""]
    audit.extend(
        f"- {row['model']} | {row['scenarioId']} | "
        f"accepted={row['accepted']} | score={row['total']} | grade={row['grade']}"
        for row in rows
    )
    (output_root / "scenario_audit.md").write_text(
        "\n".join(audit) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "outputDirectory": str(output_root),
                "resultCount": len(rows),
                "modelCount": len(scorecard),
                "answerKeysLoaded": len(rows),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
