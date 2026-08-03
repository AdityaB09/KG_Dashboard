from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _report_path(value: Path) -> Path:
    if value.is_file():
        return value
    direct = value / "model_selection_report.json"
    if direct.is_file():
        return direct
    matches = sorted(value.rglob("model_selection_report.json"))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(f"No model_selection_report.json found under {value}")
    raise RuntimeError(f"Multiple model_selection_report.json files found under {value}: {matches}")


def _run_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for run in report.get("runs") or []:
        scenario = str(run.get("scenarioId") or "")
        if scenario:
            output[scenario] = run
    return output


def _split_messages(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    return [item.strip() for item in text.split("||") if item.strip()]


def _exact_before_map(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.is_file():
        return {}
    value = _load_json(path)
    if not isinstance(value, list):
        return {}
    return {
        str(item.get("Scenario")): item
        for item in value
        if isinstance(item, dict) and item.get("Scenario")
    }


def _candidate_validation_files(report_root: Path, run: dict[str, Any]) -> list[Path]:
    candidates: list[Path] = []
    for key in ("outputDirectory", "summaryPath"):
        raw = run.get(key)
        if raw:
            path = Path(str(raw))
            directory = path if path.is_dir() else path.parent
            candidates.extend(
                directory / name
                for name in (
                    "grounding_validation_v4.json",
                    "grounding_validation.json",
                    "validation.json",
                )
            )
    scenario = str(run.get("scenarioId") or "")
    if scenario:
        candidates.extend(report_root.rglob(f"{scenario}/**/grounding_validation_v4.json"))
    return candidates


def _validation_details(report_root: Path, run: dict[str, Any]) -> dict[str, list[str]]:
    for path in _candidate_validation_files(report_root, run):
        try:
            if not path.is_file():
                continue
            value = _load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict):
            continue
        return {
            "hardErrors": _split_messages(value.get("hardErrors") or value.get("errors")),
            "qualityErrors": _split_messages(value.get("qualityErrors")),
            "contradictions": _split_messages(value.get("contradictions")),
            "unsupportedFacts": _split_messages(value.get("unsupportedFacts")),
        }
    return {"hardErrors": [], "qualityErrors": [], "contradictions": [], "unsupportedFacts": []}


def _explain_change(
    before_status: str,
    after_status: str,
    before_errors: list[str],
    before_quality: list[str],
    after_quality: list[str],
) -> str:
    if before_status == after_status:
        if after_quality:
            return "Disposition retained; quality-only review findings remain separate from safety."
        return "Disposition retained with no validator-semantic regression."

    text = " ".join(before_errors).upper()
    reasons: list[str] = []
    if "ATRIAL_FIBRILLATION" in text:
        reasons.append("historical atrial-fibrillation context is no longer treated as the current episode rhythm")
    if any(token in text for token in ("POLYMORPHIC_VT", "MONOMORPHIC_VT", "NSVT_ECTOPY")):
        reasons.append("authoritative rhythm aliases and compatible parent/child concepts are normalized before contradiction checks")
    if "BRADYCARDIA" in text:
        reasons.append("generic bradycardia language is recognized as compatible with complete heart block or junctional bradycardia")
    if "UNSUPPORTED NUMERIC CLAIMS: 120" in text:
        reasons.append("the signed axis value -120 is preserved and matched to model-visible evidence")
    if "UNSUPPORTED NUMERIC CLAIMS" in text and "120" not in text:
        reasons.append("Phase 6 decimal values and range endpoints are indexed from the model-visible evidence envelope")
    if before_quality and not after_quality:
        reasons.append("the natural-language controlled-event versus Phase 6 measurement comparison is recognized")
    if after_quality:
        reasons.append("remaining measurement-conflict findings are retained as quality-only review items")
    if not reasons:
        reasons.append("corrected validator semantics changed the disposition without changing the model response")
    return "; ".join(reasons) + "."


def main() -> None:
    parser = argparse.ArgumentParser(description="Create KGEN V6.0 to V6.0.1 validator comparison reports.")
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--before-validator-report", type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    args = parser.parse_args()

    before_report_path = _report_path(args.before.resolve())
    after_report_path = _report_path(args.after.resolve())
    before_report = _load_json(before_report_path)
    after_report = _load_json(after_report_path)
    before_runs = _run_map(before_report)
    after_runs = _run_map(after_report)
    exact_before = _exact_before_map(
        args.before_validator_report.resolve() if args.before_validator_report else None
    )

    scenarios = sorted(set(before_runs) | set(after_runs))
    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        previous = before_runs.get(scenario, {})
        current = after_runs.get(scenario, {})
        exact = exact_before.get(scenario, {})
        previous_details = _validation_details(before_report_path.parent, previous)
        current_details = _validation_details(after_report_path.parent, current)

        previous_hard = _split_messages(exact.get("HardErrors")) or previous_details["hardErrors"]
        previous_quality = _split_messages(exact.get("QualityErrors")) or previous_details["qualityErrors"]
        previous_contradictions = _split_messages(exact.get("Contradictions")) or previous_details["contradictions"]
        previous_unsupported = _split_messages(exact.get("UnsupportedFacts")) or previous_details["unsupportedFacts"]
        current_hard = current_details["hardErrors"]
        current_quality = current_details["qualityErrors"]
        current_contradictions = current_details["contradictions"]
        current_unsupported = current_details["unsupportedFacts"]

        row = {
            "scenario": scenario,
            "previousStatus": previous.get("status"),
            "newStatus": current.get("status"),
            "previousValidatorPassed": previous.get("validatorPassed"),
            "newValidatorPassed": current.get("validatorPassed"),
            "previousSafetyPass": previous.get("safetyPass"),
            "newSafetyPass": current.get("safetyPass"),
            "previousContradictionCount": previous.get("contradictionCount", len(previous_contradictions)),
            "newContradictionCount": current.get("contradictionCount", len(current_contradictions)),
            "previousUnsupportedFactCount": previous.get("unsupportedFactCount", len(previous_unsupported)),
            "newUnsupportedFactCount": current.get("unsupportedFactCount", len(current_unsupported)),
            "previousHardErrorCount": previous.get("hardErrorCount", len(previous_hard)),
            "newHardErrorCount": current.get("hardErrorCount", len(current_hard)),
            "previousQualityErrorCount": previous.get("qualityErrorCount", len(previous_quality)),
            "newQualityErrorCount": current.get("qualityErrorCount", len(current_quality)),
            "qualityWarningsRetained": current_quality,
            "previousHardErrors": previous_hard,
            "newHardErrors": current_hard,
            "previousContradictions": previous_contradictions,
            "newContradictions": current_contradictions,
            "previousUnsupportedFacts": previous_unsupported,
            "newUnsupportedFacts": current_unsupported,
            "scoreBefore": previous.get("totalScore"),
            "scoreAfter": current.get("totalScore"),
        }
        row["explanation"] = _explain_change(
            str(row["previousStatus"] or ""),
            str(row["newStatus"] or ""),
            previous_hard,
            previous_quality,
            current_quality,
        )
        rows.append(row)

    payload = {
        "schemaVersion": "kgen-v6-0-1-before-after-validator-comparison-v1",
        "repairName": "KGEN V6.0.1 Validator Semantics Repair",
        "beforeModelSelectionReport": str(before_report_path),
        "afterModelSelectionReport": str(after_report_path),
        "scenarioCount": len(rows),
        "rows": rows,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    csv_fields = [
        "scenario",
        "previousStatus",
        "newStatus",
        "previousValidatorPassed",
        "newValidatorPassed",
        "previousSafetyPass",
        "newSafetyPass",
        "previousContradictionCount",
        "newContradictionCount",
        "previousUnsupportedFactCount",
        "newUnsupportedFactCount",
        "previousHardErrorCount",
        "newHardErrorCount",
        "previousQualityErrorCount",
        "newQualityErrorCount",
        "qualityWarningsRetained",
        "scoreBefore",
        "scoreAfter",
        "explanation",
    ]
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: " || ".join(row[key]) if isinstance(row.get(key), list) else row.get(key)
                    for key in csv_fields
                }
            )

    print(json.dumps({"status": "ok", "scenarioCount": len(rows), "json": str(args.output_json), "csv": str(args.output_csv)}, indent=2))


if __name__ == "__main__":
    main()
