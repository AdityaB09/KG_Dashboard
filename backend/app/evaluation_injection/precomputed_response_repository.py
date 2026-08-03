from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PrecomputedResponseError(RuntimeError):
    """Raised when a configured precomputed demo response cannot be resolved."""


DEFAULT_EVALUATED_FOLDER = "medgemma-27b-it-all8-incart-v6-0-1"
DEFAULT_MODEL_SLUG = "google-medgemma-27b-it"
DEFAULT_MODEL_NAME = "google/medgemma-27b-it"

REQUIRED_SCENARIOS = (
    "VFIB-STEMI-001",
    "TORSADES-LQT-002",
    "VT-ISCHEMIC-003",
    "AFIB-RVR-SEPSIS-004",
    "CHB-HYPERK-005",
    "BRADY-DIGTOX-006",
    "SVT-PSVT-007",
    "NSVT-ECTOPY-008",
)


@dataclass(frozen=True)
class PrecomputedResponseArtifacts:
    scenario_id: str
    run_directory: Path
    widget: dict[str, Any]
    cardinal: dict[str, Any]
    validation: dict[str, Any]
    score: dict[str, Any]
    benchmark: dict[str, Any]
    diagnostic_event: dict[str, Any]
    run_summary: dict[str, Any]


def _flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def precomputed_demo_enabled() -> bool:
    return _flag("PRECOMPUTED_SLM_DEMO_ENABLED", False)


def precomputed_demo_required() -> bool:
    return _flag("PRECOMPUTED_SLM_DEMO_REQUIRED", True)


def precomputed_demo_delay_seconds() -> float:
    raw = os.getenv("PRECOMPUTED_SLM_DELAY_SECONDS", "5").strip()
    try:
        return min(30.0, max(0.0, float(raw)))
    except ValueError as error:
        raise PrecomputedResponseError(
            "PRECOMPUTED_SLM_DELAY_SECONDS must be numeric."
        ) from error


def _backend_root() -> Path:
    # .../backend/app/evaluation_injection/precomputed_response_repository.py
    return Path(__file__).resolve().parents[2]


def precomputed_root() -> Path:
    configured = os.getenv("PRECOMPUTED_SLM_ROOT", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = _backend_root() / path
        return path.resolve()

    evaluated_folder = os.getenv(
        "PRECOMPUTED_SLM_EVALUATED_FOLDER",
        DEFAULT_EVALUATED_FOLDER,
    ).strip() or DEFAULT_EVALUATED_FOLDER

    return (
        _backend_root()
        / "data"
        / "colab_model_benchmark"
        / "evaluated"
        / evaluated_folder
    ).resolve()


def precomputed_model_slug() -> str:
    return (
        os.getenv("PRECOMPUTED_SLM_MODEL_SLUG", DEFAULT_MODEL_SLUG).strip()
        or DEFAULT_MODEL_SLUG
    )


def precomputed_model_name() -> str:
    return (
        os.getenv("PRECOMPUTED_SLM_MODEL_NAME", DEFAULT_MODEL_NAME).strip()
        or DEFAULT_MODEL_NAME
    )


def precomputed_run_number() -> int:
    raw = os.getenv("PRECOMPUTED_SLM_RUN", "1").strip()
    try:
        return max(1, int(raw))
    except ValueError as error:
        raise PrecomputedResponseError(
            "PRECOMPUTED_SLM_RUN must be an integer."
        ) from error


def _read_json(path: Path, *, required: bool = True) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if not required:
            return {}
        raise PrecomputedResponseError(
            f"Precomputed response artifact was not found: {path}"
        )
    except (OSError, json.JSONDecodeError) as error:
        raise PrecomputedResponseError(
            f"Could not read precomputed response artifact: {path}"
        ) from error

    if not isinstance(value, dict):
        raise PrecomputedResponseError(
            f"Precomputed response artifact is not a JSON object: {path}"
        )
    return value


def _model_directory(root: Path) -> Path:
    slug = precomputed_model_slug()

    # Supported layouts:
    #   root/google-medgemma-27b-it/<scenario>/run-1
    #   root/<scenario>/run-1
    #   root itself is google-medgemma-27b-it
    if root.name == slug:
        return root

    nested = root / slug
    if nested.is_dir():
        return nested

    if any((root / scenario).is_dir() for scenario in REQUIRED_SCENARIOS):
        return root

    return nested


def scenario_run_directory(scenario_id: str) -> Path:
    normalized = str(scenario_id or "").strip().upper()
    if normalized not in REQUIRED_SCENARIOS:
        raise PrecomputedResponseError(
            f"Unsupported precomputed demo scenario: {normalized or '<empty>'}"
        )

    return (
        _model_directory(precomputed_root())
        / normalized
        / f"run-{precomputed_run_number()}"
    )


def load_precomputed_response(
    scenario_id: str,
) -> PrecomputedResponseArtifacts:
    run_directory = scenario_run_directory(scenario_id)
    normalized = str(scenario_id).strip().upper()

    widget = _read_json(run_directory / "slm_widget_result_v4.json")
    cardinal = _read_json(run_directory / "cardinal_model_response.json")
    validation = _read_json(
        run_directory / "grounding_validation_v4.json",
        required=False,
    )
    score = _read_json(
        run_directory / "evaluation_score.json",
        required=False,
    )
    benchmark = _read_json(
        run_directory / "benchmark_result_v4.json",
        required=False,
    )
    diagnostic_event = _read_json(
        run_directory / "diagnostic_event.json",
        required=False,
    )
    run_summary = _read_json(
        run_directory / "run_summary.json",
        required=False,
    )

    declared = str(
        widget.get("scenarioId")
        or cardinal.get("scenarioId")
        or run_summary.get("scenarioId")
        or ""
    ).strip().upper()

    if declared != normalized:
        raise PrecomputedResponseError(
            "Precomputed artifact scenario mismatch. "
            f"requested={normalized}; declared={declared or '<missing>'}; "
            f"directory={run_directory}"
        )

    response = (
        cardinal.get("displayModelResponse")
        or cardinal.get("modelResponse")
        or {}
    )
    if not isinstance(response, dict) or not str(
        response.get("episodeSummary") or ""
    ).strip():
        raise PrecomputedResponseError(
            f"Precomputed response is missing displayable model content: {run_directory}"
        )

    return PrecomputedResponseArtifacts(
        scenario_id=normalized,
        run_directory=run_directory,
        widget=widget,
        cardinal=cardinal,
        validation=validation,
        score=score,
        benchmark=benchmark,
        diagnostic_event=diagnostic_event,
        run_summary=run_summary,
    )


def precomputed_demo_status() -> dict[str, Any]:
    available: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    for scenario_id in REQUIRED_SCENARIOS:
        try:
            artifacts = load_precomputed_response(scenario_id)
            available.append(
                {
                    "scenarioId": scenario_id,
                    "available": True,
                    "validationStatus": artifacts.widget.get("validationStatus"),
                    "benchmarkScore": (
                        artifacts.widget.get("benchmark") or {}
                    ).get("score"),
                    "model": (
                        artifacts.widget.get("model") or {}
                    ).get("name") or precomputed_model_name(),
                }
            )
        except PrecomputedResponseError as error:
            missing.append(
                {
                    "scenarioId": scenario_id,
                    "available": False,
                    "error": str(error),
                }
            )

    return {
        "schemaVersion": "precomputed-slm-demo-status-v1",
        "enabled": precomputed_demo_enabled(),
        "required": precomputed_demo_required(),
        "provider": "precomputed_lightning_artifact",
        "model": precomputed_model_name(),
        "lookupMode": "scenario_id",
        "liveInference": False,
        "run": precomputed_run_number(),
        "delaySeconds": precomputed_demo_delay_seconds(),
        "scenarioCount": len(REQUIRED_SCENARIOS),
        "availableCount": len(available),
        "missingCount": len(missing),
        "allScenariosReady": not missing,
        "scenarios": available + missing,
    }
