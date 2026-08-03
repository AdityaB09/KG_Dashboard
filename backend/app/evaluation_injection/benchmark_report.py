from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    return payload if isinstance(payload, dict) else None


def collect_run_summaries(output_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for path in output_root.rglob("run_summary.json"):
        payload = _read_json(path)
        if not payload:
            continue

        rows.append({**payload, "summaryPath": str(path)})

    return rows


def _rate(
    items: list[dict[str, Any]],
    field: str,
) -> float:
    if not items:
        return 0.0
    return sum(1 for item in items if item.get(field) is True) / len(items)


def _aggregate(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        grouped[str(row.get(key) or "unknown")].append(row)

    output: dict[str, Any] = {}

    for group, items in grouped.items():
        scores = [
            float(item["totalScore"])
            for item in items
            if isinstance(item.get("totalScore"), (int, float))
        ]
        runtimes = [
            float(item["elapsedSeconds"])
            for item in items
            if isinstance(item.get("elapsedSeconds"), (int, float))
        ]

        contradiction_runs = sum(
            1 for item in items
            if int(item.get("contradictionCount") or 0) > 0
        )
        unsupported_runs = sum(
            1 for item in items
            if int(item.get("unsupportedFactCount") or 0) > 0
        )

        output[group] = {
            "runCount": len(items),
            "generationSuccessCount": sum(1 for item in items if item.get("generationSuccess") is True),
            "generationSuccessRate": _rate(items, "generationSuccess"),
            "validContractCount": sum(1 for item in items if item.get("validContract") is True),
            "validContractRate": _rate(items, "validContract"),
            "strictGroundingAcceptanceCount": sum(1 for item in items if item.get("strictGroundingAccepted") is True),
            "strictGroundingAcceptanceRate": _rate(items, "strictGroundingAccepted"),
            "acceptedWithReviewCount": sum(1 for item in items if item.get("acceptedWithReview") is True),
            "acceptedWithReviewRate": _rate(items, "acceptedWithReview"),
            "validatorPassCount": sum(1 for item in items if item.get("validatorPassed") is True),
            "validatorPassRate": _rate(items, "validatorPassed"),
            "groundingPassCount": sum(1 for item in items if item.get("groundingPass") is True),
            "groundingPassRate": _rate(items, "groundingPass"),
            "validatorDisplayCount": sum(1 for item in items if item.get("validatorDisplayable") is True),
            "validatorDisplayRate": _rate(items, "validatorDisplayable"),
            "safetyPassCount": sum(1 for item in items if item.get("safetyPass") is True),
            "safetyPassRate": _rate(items, "safetyPass"),
            "benchmarkPassCount": sum(1 for item in items if item.get("benchmarkPass") is True),
            "benchmarkPassRate": _rate(items, "benchmarkPass"),
            "overallPassCount": sum(1 for item in items if item.get("overallPass") is True),
            "overallPassRate": _rate(items, "overallPass"),
            "firstAttemptAcceptedCount": sum(1 for item in items if item.get("firstAttemptAccepted") is True),
            "firstAttemptAcceptanceRate": _rate(items, "firstAttemptAccepted"),
            "contradictionRunCount": contradiction_runs,
            "contradictionRunRate": contradiction_runs / len(items) if items else 0.0,
            "unsupportedFactRunCount": unsupported_runs,
            "unsupportedFactRunRate": unsupported_runs / len(items) if items else 0.0,
            "contradictionCount": sum(int(item.get("contradictionCount") or 0) for item in items),
            "unsupportedFactCount": sum(int(item.get("unsupportedFactCount") or 0) for item in items),
            "evidenceInvalidCount": sum(1 for item in items if item.get("groundingStatus") == "evidence_invalid"),
            "loadFailureCount": sum(1 for item in items if item.get("status") == "model_load_failed"),
            "averageBenchmarkScore": sum(scores) / len(scores) if scores else None,
            "minimumScenarioScore": min(scores) if scores else None,
            "maximumScenarioScore": max(scores) if scores else None,
            "averageRuntimeSeconds": sum(runtimes) / len(runtimes) if runtimes else None,
            # Compatibility names retained for existing report consumers.
            "successfulRunCount": sum(1 for item in items if item.get("generationSuccess") is True),
            "averageScore": sum(scores) / len(scores) if scores else None,
            "minimumScore": min(scores) if scores else None,
            "maximumScore": max(scores) if scores else None,
        }

    return output


def generate_benchmark_report(output_root: Path) -> dict[str, Any]:
    rows = collect_run_summaries(output_root)
    report = {
        "schemaVersion": "universal-grounded-benchmark-summary-v2",
        "runCount": len(rows),
        "models": _aggregate(rows, "modelId"),
        "scenarios": _aggregate(rows, "scenarioId"),
        "runs": rows,
    }

    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "benchmark_summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    columns = (
        "modelId",
        "model",
        "scenarioId",
        "runNumber",
        "status",
        "generationSuccess",
        "validContract",
        "groundingStatus",
        "validatorPassed",
        "groundingPass",
        "strictGroundingAccepted",
        "acceptedWithReview",
        "validatorDisplayable",
        "hardErrorCount",
        "qualityErrorCount",
        "totalScore",
        "benchmarkPass",
        "benchmarkDisposition",
        "safetyPass",
        "overallPass",
        "overallDisposition",
        "firstAttemptAccepted",
        "attemptCount",
        "contradictionCount",
        "unsupportedFactCount",
        "evidenceCoverageCount",
        "evidenceCoverageRequired",
        "elapsedSeconds",
        "errorType",
    )

    with (output_root / "benchmark_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})

    return report
