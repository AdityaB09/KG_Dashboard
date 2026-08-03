from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.evaluation_injection.benchmark_report import (
    generate_benchmark_report,
)
from app.evaluation_injection.canonical_episode_repository import (
    canonical_episode_dir,
    list_canonical_scenarios,
)
from app.evaluation_injection.cardinal_bridge import (
    rerun_grounded_from_saved_input,
)
from app.evaluation_injection.model_registry import (
    list_models,
    resolve_model,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return normalized[:120] or "run"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def _default_output_root() -> Path:
    return Path(settings.EPISODE_STORAGE_PATH).parent / "universal_benchmarks"


def _episode_dir(episode_id: str) -> Path:
    path = Path(settings.EPISODE_STORAGE_PATH) / episode_id

    if not path.exists():
        raise FileNotFoundError(f"Episode directory not found: {path}")

    return path


def _input_metadata(source_dir: Path) -> tuple[str, str]:
    input_path = source_dir / "grounded_model_input.json"
    if not input_path.exists():
        raise FileNotFoundError(f"grounded_model_input.json not found: {input_path}")

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    scenario_id = str(
        payload.get("scenarioId")
        or ((payload.get("diagnosticEvent") or {}).get("source") or {}).get("identifier")
        or source_dir.name
    )
    episode_id = str(payload.get("episodeId") or source_dir.name)
    return scenario_id, episode_id


def _next_run_dir(
    *,
    output_root: Path,
    model_id: str,
    scenario_id: str,
) -> tuple[Path, int]:
    group = output_root / _slug(model_id) / _slug(scenario_id)
    group.mkdir(parents=True, exist_ok=True)

    index = 1
    while True:
        candidate = group / f"run-{index}"
        if not candidate.exists():
            candidate.mkdir(parents=False, exist_ok=False)
            return candidate, index
        index += 1


def _classify_failure(error: BaseException) -> str:
    text = str(error).lower()

    if any(
        marker in text
        for marker in (
            "out of memory",
            "not enough memory",
            "memory allocation",
            "failed to allocate",
            "cannot allocate",
            "model requires more system memory",
            "insufficient memory",
        )
    ):
        return "ollama_memory_allocation"

    if any(
        marker in text
        for marker in (
            "connection refused",
            "could not reach ollama",
            "connecterror",
            "connection error",
        )
    ):
        return "ollama_unavailable"

    if "timeout" in text:
        return "model_timeout"

    return "model_run_failed"


async def run_one(
    *,
    source_dir: Path,
    model_record: dict[str, Any],
    output_root: Path,
) -> dict[str, Any]:
    scenario_id, episode_id = _input_metadata(source_dir)
    model_id = str(model_record.get("id") or model_record.get("ollamaName"))
    ollama_name = str(model_record.get("ollamaName") or model_id)
    run_dir, run_number = _next_run_dir(
        output_root=output_root,
        model_id=model_id,
        scenario_id=scenario_id,
    )

    base = {
        "schemaVersion": "universal-grounded-run-summary-v1",
        "status": "running",
        "startedAt": _now_iso(),
        "modelId": model_id,
        "model": ollama_name,
        "modelDisplayName": model_record.get("displayName") or model_id,
        "scenarioId": scenario_id,
        "episodeId": episode_id,
        "runNumber": run_number,
        "sourceDirectory": str(source_dir),
        "outputDirectory": str(run_dir),
    }
    summary_path = run_dir / "run_summary.json"
    _atomic_json(summary_path, base)

    try:
        result = await rerun_grounded_from_saved_input(
            episode_dir=source_dir,
            model_override=ollama_name,
            artifact_dir=run_dir,
            update_phase7_storage=False,
        )
    except Exception as exc:
        error_type = _classify_failure(exc)
        failed = {
            **base,
            "status": "model_load_failed" if error_type == "ollama_memory_allocation" else "failed",
            "completedAt": _now_iso(),
            "errorType": error_type,
            "error": str(exc),
        }
        _atomic_json(summary_path, failed)
        return failed

    score = result.get("score") or {}
    reliability = result.get("reliability") or {}
    model_metadata = result.get("model") or {}
    validation = result.get("validation") or {}
    grounding = score.get("grounding") or {}
    benchmark = score.get("benchmark") or {}

    validation_status = str(
        grounding.get("status")
        or validation.get("groundingStatus")
        or validation.get("status")
        or "unknown"
    )
    validator_passed = bool(
        validation.get("validatorPassed")
        if "validatorPassed" in validation
        else validation.get("hardAccepted")
        if "hardAccepted" in validation
        else validation.get("accepted") or validation.get("displayableWithReview")
    )

    completed = {
        **base,
        "status": (
            validation_status
            if validation_status in {
                "accepted",
                "accepted_with_review",
                "rejected",
                "configuration_error",
                "evidence_invalid",
                "generation_failed",
            }
            else result.get("status") or "complete"
        ),
        "pipelineStatus": result.get("status") or "complete",
        "completedAt": _now_iso(),
        "validation": validation,
        "generationSuccess": bool(result.get("generationAttempted", True)),
        "validContract": bool(result.get("validContract", True)),
        "groundingStatus": validation_status,
        "validatorPassed": validator_passed,
        "groundingPass": bool(score.get("groundingPass", validator_passed)),
        "strictGroundingAccepted": bool(validation.get("accepted")),
        "acceptedWithReview": bool(validation.get("displayableWithReview")),
        "validatorDisplayable": bool(validation.get("accepted") or validation.get("displayableWithReview")),
        "hardErrorCount": len(validation.get("hardErrors") or []),
        "qualityErrorCount": len(validation.get("qualityErrors") or []),
        "totalScore": score.get("total"),
        "grade": score.get("grade"),
        "overallDisposition": score.get("overallDisposition"),
        "overallPass": score.get("overallPass"),
        "safetyPass": score.get("safetyPass"),
        "benchmarkPass": benchmark.get("pass") if "pass" in benchmark else score.get("benchmarkPass"),
        "benchmarkDisposition": benchmark.get("disposition") or score.get("benchmarkDisposition"),
        "firstAttemptAccepted": reliability.get("firstAttemptAccepted"),
        "attemptCount": reliability.get("attemptCount"),
        "contradictionCount": reliability.get("contradictionCount"),
        "unsupportedFactCount": reliability.get("unsupportedFactCount"),
        "evidenceCoverageCount": reliability.get("evidenceCoverageCount"),
        "evidenceCoverageRequired": reliability.get("evidenceCoverageRequired"),
        "elapsedSeconds": model_metadata.get("elapsedSeconds"),
        "responseFile": result.get("responseFile"),
    }
    _atomic_json(summary_path, completed)
    return completed


async def run_matrix(
    *,
    sources: list[Path],
    models: list[dict[str, Any]],
    runs: int,
    output_root: Path,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for source in sources:
        for model in models:
            for _ in range(runs):
                result = await run_one(
                    source_dir=source,
                    model_record=model,
                    output_root=output_root,
                )
                results.append(result)
                print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)

    generate_benchmark_report(output_root)
    return results


def _resolve_sources(
    *,
    episode_id: str | None,
    scenario_ids: list[str],
    all_scenarios: bool,
) -> list[Path]:
    if episode_id:
        return [_episode_dir(episode_id)]

    if all_scenarios:
        scenario_ids = list_canonical_scenarios()

    if not scenario_ids:
        raise ValueError(
            "Provide --episode-id, one or more --scenario-id values, or --all-scenarios."
        )

    return [canonical_episode_dir(scenario_id) for scenario_id in scenario_ids]


def _resolve_models(model_ids: list[str], all_models: bool) -> list[dict[str, Any]]:
    if all_models:
        models = list_models(enabled_only=True)
    else:
        models = [resolve_model(model_id) for model_id in model_ids]

    if not models:
        raise ValueError("No enabled models were selected.")

    return models


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run one or more Ollama models against saved evaluation evidence. "
            "No waveform replay or reinjection is performed."
        )
    )
    parser.add_argument("--episode-id", default=None)
    parser.add_argument("--scenario-id", action="append", default=[])
    parser.add_argument("--all-scenarios", action="store_true")
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--all-models", action="store_true")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--output-root", default=None)
    arguments = parser.parse_args()

    if arguments.runs < 1:
        raise ValueError("--runs must be at least 1.")

    sources = _resolve_sources(
        episode_id=arguments.episode_id,
        scenario_ids=arguments.scenario_id,
        all_scenarios=arguments.all_scenarios,
    )
    models = _resolve_models(arguments.model, arguments.all_models)
    output_root = Path(arguments.output_root) if arguments.output_root else _default_output_root()

    asyncio.run(
        run_matrix(
            sources=sources,
            models=models,
            runs=arguments.runs,
            output_root=output_root,
        )
    )


if __name__ == "__main__":
    main()
