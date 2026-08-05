from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PrecomputedResponseError(RuntimeError):
    """Raised when a configured precomputed response cannot be resolved safely."""


DEFAULT_EVALUATED_FOLDER = "medgemma-v6-0-3-1-dual"
DEFAULT_PROFILE = "curated"
DEFAULT_RUN = 2
DEFAULT_RESPONSE_SET_ID = "medgemma-dual-v6-0-3-1"
DEFAULT_RESPONSE_CONTRACT_VERSION = "model-clinical-output-v6.0.3"
DEFAULT_VALIDATION_VERSION = "v6.0.3.1"

PROFILE_ALIASES = {
    "27b-it": "medgemma-27b-it",
    "medgemma-27b-it": "medgemma-27b-it",
    "google-medgemma-27b-it": "medgemma-27b-it",
    "google/medgemma-27b-it": "medgemma-27b-it",
    "27b-text-it": "medgemma-27b-text-it",
    "text-it": "medgemma-27b-text-it",
    "medgemma-27b-text-it": "medgemma-27b-text-it",
    "google-medgemma-27b-text-it": "medgemma-27b-text-it",
    "google/medgemma-27b-text-it": "medgemma-27b-text-it",
    "curated": "curated",
    "curated-best": "curated",
    "curated-medgemma-v6-0-3-1": "curated",
}

BUILTIN_PROFILES: dict[str, dict[str, str]] = {
    "medgemma-27b-it": {
        "modelSlug": "google-medgemma-27b-it",
        "modelName": "google/medgemma-27b-it",
        "selectionMode": "single_model",
    },
    "medgemma-27b-text-it": {
        "modelSlug": "google-medgemma-27b-text-it",
        "modelName": "google/medgemma-27b-text-it",
        "selectionMode": "single_model",
    },
    "curated": {
        "modelSlug": "curated-medgemma-v6-0-3-1",
        "modelName": "Curated MedGemma V6.0.3.1",
        "selectionMode": "curated_best_per_scenario",
    },
}

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


MODEL_RESPONSE_KEYS = (
    "episodeSummary",
    "mostLikelyEtiologyAndClinicalContext",
    "contributingFactors",
    "materialEtiologicUncertainty",
)


def _validate_four_field_response(
    response: dict[str, Any],
    *,
    run_directory: Path,
) -> None:
    actual_keys = set(response)
    expected_keys = set(MODEL_RESPONSE_KEYS)
    if actual_keys != expected_keys:
        raise PrecomputedResponseError(
            "Precomputed response does not match the exact V6.0.3 "
            "four-field contract. "
            f"expected={sorted(expected_keys)}; actual={sorted(actual_keys)}; "
            f"directory={run_directory}"
        )

    for field in (
        "episodeSummary",
        "mostLikelyEtiologyAndClinicalContext",
    ):
        if not isinstance(response.get(field), str) or not response[field].strip():
            raise PrecomputedResponseError(
                f"Precomputed response field {field!r} must be a non-empty string: "
                f"{run_directory}"
            )

    contributing = response.get("contributingFactors")
    if (
        not isinstance(contributing, list)
        or not 1 <= len(contributing) <= 5
        or any(not isinstance(item, str) or not item.strip() for item in contributing)
    ):
        raise PrecomputedResponseError(
            "Precomputed contributingFactors must contain 1 to 5 non-empty strings: "
            f"{run_directory}"
        )

    uncertainty = response.get("materialEtiologicUncertainty")
    if (
        not isinstance(uncertainty, list)
        or len(uncertainty) > 2
        or any(not isinstance(item, str) or not item.strip() for item in uncertainty)
    ):
        raise PrecomputedResponseError(
            "Precomputed materialEtiologicUncertainty must contain 0 to 2 "
            f"non-empty strings: {run_directory}"
        )


@dataclass(frozen=True)
class PrecomputedResponseArtifacts:
    scenario_id: str
    profile: str
    response_set_id: str
    source_model: str
    selection_mode: str
    run_directory: Path
    widget: dict[str, Any]
    cardinal: dict[str, Any]
    validation: dict[str, Any]
    score: dict[str, Any]
    benchmark: dict[str, Any]
    diagnostic_event: dict[str, Any]
    run_summary: dict[str, Any]
    installation_metadata: dict[str, Any]


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
    raw = os.getenv("PRECOMPUTED_SLM_DELAY_SECONDS", "2").strip()
    try:
        return min(30.0, max(0.0, float(raw)))
    except ValueError as error:
        raise PrecomputedResponseError(
            "PRECOMPUTED_SLM_DELAY_SECONDS must be numeric."
        ) from error


def _backend_root() -> Path:
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


def response_set_manifest(*, required: bool = True) -> dict[str, Any]:
    return _read_json(
        precomputed_root() / "response_set_manifest.json",
        required=required,
    )


def precomputed_available_profiles() -> list[dict[str, Any]]:
    manifest = response_set_manifest(required=False)
    profiles = dict(BUILTIN_PROFILES)
    declared = manifest.get("profiles") if isinstance(manifest, dict) else None
    if isinstance(declared, dict):
        for key, value in declared.items():
            if isinstance(value, dict):
                merged = dict(profiles.get(str(key)) or {})
                merged.update(value)
                profiles[str(key)] = merged
    return [
        {
            "profile": key,
            "modelSlug": value.get("modelSlug"),
            "modelName": value.get("modelName"),
            "selectionMode": value.get("selectionMode"),
        }
        for key, value in sorted(profiles.items())
    ]


def precomputed_profile() -> str:
    raw = os.getenv("PRECOMPUTED_SLM_PROFILE", DEFAULT_PROFILE).strip().lower()
    profile = PROFILE_ALIASES.get(raw, raw)
    manifest = response_set_manifest(required=False)
    profiles = manifest.get("profiles") if isinstance(manifest, dict) else None
    allowed = set(BUILTIN_PROFILES)
    if isinstance(profiles, dict):
        allowed.update(str(key) for key in profiles)
    if profile not in allowed:
        raise PrecomputedResponseError(
            "Unsupported PRECOMPUTED_SLM_PROFILE. "
            f"received={raw!r}; allowed={sorted(allowed)}"
        )
    return profile


def _profile_config() -> dict[str, Any]:
    profile = precomputed_profile()
    config = dict(BUILTIN_PROFILES.get(profile) or {})
    manifest = response_set_manifest(required=False)
    profiles = manifest.get("profiles") if isinstance(manifest, dict) else None
    if isinstance(profiles, dict) and isinstance(profiles.get(profile), dict):
        config.update(profiles[profile])
    if not config.get("modelSlug"):
        raise PrecomputedResponseError(
            f"No modelSlug is configured for precomputed profile {profile!r}."
        )
    return config


def _configured_or_profile(name: str, config_key: str) -> str:
    explicit = os.getenv(name, "").strip()
    configured = str(_profile_config().get(config_key) or "").strip()
    if explicit and configured and explicit != configured:
        raise PrecomputedResponseError(
            f"{name}={explicit!r} conflicts with PRECOMPUTED_SLM_PROFILE="
            f"{precomputed_profile()!r}, which requires {configured!r}."
        )
    return explicit or configured


def precomputed_model_slug() -> str:
    return _configured_or_profile("PRECOMPUTED_SLM_MODEL_SLUG", "modelSlug")


def precomputed_model_name() -> str:
    return _configured_or_profile("PRECOMPUTED_SLM_MODEL_NAME", "modelName")


def precomputed_selection_mode() -> str:
    return str(_profile_config().get("selectionMode") or "single_model")


def precomputed_run_number() -> int:
    manifest = response_set_manifest(required=False)
    manifest_run = manifest.get("sourceRun") if isinstance(manifest, dict) else None
    raw = os.getenv(
        "PRECOMPUTED_SLM_RUN",
        str(manifest_run or DEFAULT_RUN),
    ).strip()
    try:
        return max(1, int(raw))
    except ValueError as error:
        raise PrecomputedResponseError(
            "PRECOMPUTED_SLM_RUN must be an integer."
        ) from error


def precomputed_artifact_set_id() -> str:
    explicit = os.getenv("PRECOMPUTED_SLM_RESPONSE_SET_ID", "").strip()
    manifest = response_set_manifest(required=False)
    declared = str(manifest.get("responseSetId") or DEFAULT_RESPONSE_SET_ID)
    if explicit and explicit != declared:
        raise PrecomputedResponseError(
            "PRECOMPUTED_SLM_RESPONSE_SET_ID does not match the installed set. "
            f"configured={explicit!r}; installed={declared!r}"
        )
    return explicit or declared


def precomputed_response_contract_version() -> str:
    manifest = response_set_manifest(required=False)
    return str(
        manifest.get("responseContractVersion")
        or DEFAULT_RESPONSE_CONTRACT_VERSION
    )


def precomputed_validation_version() -> str:
    manifest = response_set_manifest(required=False)
    return str(manifest.get("validationVersion") or DEFAULT_VALIDATION_VERSION)


def _model_directory(root: Path) -> Path:
    slug = precomputed_model_slug()
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


def _validate_installed_identity(
    *,
    scenario_id: str,
    run_directory: Path,
    metadata: dict[str, Any],
) -> None:
    if not metadata:
        raise PrecomputedResponseError(
            f"installation_metadata.json is required: {run_directory}"
        )
    checks = {
        "scenarioId": scenario_id,
        "runNumber": precomputed_run_number(),
        "responseSetId": precomputed_artifact_set_id(),
        "responseContractVersion": precomputed_response_contract_version(),
        "validationVersion": precomputed_validation_version(),
    }
    for key, expected in checks.items():
        actual = metadata.get(key)
        if actual != expected:
            raise PrecomputedResponseError(
                "Installed precomputed artifact identity mismatch. "
                f"field={key}; expected={expected!r}; actual={actual!r}; "
                f"directory={run_directory}"
            )
    installed_profile = str(metadata.get("profile") or "")
    if installed_profile != precomputed_profile():
        raise PrecomputedResponseError(
            "Installed response profile mismatch. "
            f"expected={precomputed_profile()!r}; actual={installed_profile!r}"
        )


def load_precomputed_response(scenario_id: str) -> PrecomputedResponseArtifacts:
    run_directory = scenario_run_directory(scenario_id)
    normalized = str(scenario_id).strip().upper()

    widget = _read_json(run_directory / "slm_widget_result_v4.json")
    cardinal = _read_json(run_directory / "cardinal_model_response.json")
    validation = _read_json(
        run_directory / "grounding_validation_v4.json", required=False
    )
    score = _read_json(run_directory / "evaluation_score.json", required=False)
    benchmark = _read_json(
        run_directory / "benchmark_result_v4.json", required=False
    )
    diagnostic_event = _read_json(
        run_directory / "diagnostic_event.json", required=False
    )
    run_summary = _read_json(run_directory / "run_summary.json", required=False)
    installation_metadata = _read_json(
        run_directory / "installation_metadata.json"
    )

    _validate_installed_identity(
        scenario_id=normalized,
        run_directory=run_directory,
        metadata=installation_metadata,
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

    response = cardinal.get("displayModelResponse") or cardinal.get("modelResponse") or {}
    if not isinstance(response, dict):
        raise PrecomputedResponseError(
            f"Precomputed response is not a JSON object: {run_directory}"
        )
    _validate_four_field_response(response, run_directory=run_directory)

    validation_passed = bool(
        validation.get("validatorPassed", validation.get("accepted"))
    )
    if not validation_passed:
        raise PrecomputedResponseError(
            f"Installed response is not validator-passed: {run_directory}"
        )
    if validation.get("unsupportedFacts") or validation.get("contradictions"):
        raise PrecomputedResponseError(
            f"Installed response has grounding failures: {run_directory}"
        )

    source_model = str(
        installation_metadata.get("sourceModel")
        or (cardinal.get("model") or {}).get("name")
        or precomputed_model_name()
    )

    return PrecomputedResponseArtifacts(
        scenario_id=normalized,
        profile=precomputed_profile(),
        response_set_id=precomputed_artifact_set_id(),
        source_model=source_model,
        selection_mode=str(
            installation_metadata.get("selectionMode")
            or precomputed_selection_mode()
        ),
        run_directory=run_directory,
        widget=widget,
        cardinal=cardinal,
        validation=validation,
        score=score,
        benchmark=benchmark,
        diagnostic_event=diagnostic_event,
        run_summary=run_summary,
        installation_metadata=installation_metadata,
    )


def precomputed_demo_status() -> dict[str, Any]:
    available: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    manifest_error: str | None = None
    try:
        manifest = response_set_manifest(required=True)
    except PrecomputedResponseError as error:
        manifest = {}
        manifest_error = str(error)

    for scenario_id in REQUIRED_SCENARIOS:
        try:
            artifacts = load_precomputed_response(scenario_id)
            available.append(
                {
                    "scenarioId": scenario_id,
                    "available": True,
                    "sourceModel": artifacts.source_model,
                    "validationStatus": artifacts.validation.get("groundingStatus")
                    or artifacts.validation.get("status"),
                    "validatorPassed": bool(
                        artifacts.validation.get(
                            "validatorPassed",
                            artifacts.validation.get("accepted"),
                        )
                    ),
                    "benchmarkScore": (
                        artifacts.widget.get("benchmark") or {}
                    ).get("score")
                    or artifacts.score.get("total"),
                    "runDirectory": str(artifacts.run_directory),
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
        "schemaVersion": "precomputed-slm-demo-status-v2",
        "enabled": precomputed_demo_enabled(),
        "required": precomputed_demo_required(),
        "provider": "precomputed_lightning_artifact",
        "profile": precomputed_profile(),
        "responseSetId": precomputed_artifact_set_id(),
        "selectionMode": precomputed_selection_mode(),
        "model": precomputed_model_name(),
        "modelSlug": precomputed_model_slug(),
        "lookupMode": "scenario_id",
        "liveInference": False,
        "generationAttempted": False,
        "run": precomputed_run_number(),
        "delaySeconds": precomputed_demo_delay_seconds(),
        "responseContractVersion": precomputed_response_contract_version(),
        "validationVersion": precomputed_validation_version(),
        "manifestSchemaVersion": manifest.get("schemaVersion"),
        "manifestError": manifest_error,
        "scenarioCount": len(REQUIRED_SCENARIOS),
        "availableCount": len(available),
        "missingCount": len(missing),
        "allScenariosReady": not missing and manifest_error is None,
        "scenarios": available + missing,
    }
