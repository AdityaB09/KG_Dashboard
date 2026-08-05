from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import types
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCENARIOS = (
    "VFIB-STEMI-001",
    "TORSADES-LQT-002",
    "VT-ISCHEMIC-003",
    "AFIB-RVR-SEPSIS-004",
    "CHB-HYPERK-005",
    "BRADY-DIGTOX-006",
    "SVT-PSVT-007",
    "NSVT-ECTOPY-008",
)

MODEL_PROFILES = {
    "medgemma-27b-it": {
        "model": "google/medgemma-27b-it",
        "slug": "google-medgemma-27b-it",
        "name": "google/medgemma-27b-it",
        "selectionMode": "single_model",
    },
    "medgemma-27b-text-it": {
        "model": "google/medgemma-27b-text-it",
        "slug": "google-medgemma-27b-text-it",
        "name": "google/medgemma-27b-text-it",
        "selectionMode": "single_model",
    },
    "curated": {
        "model": None,
        "slug": "curated-medgemma-v6-0-3-1",
        "name": "Curated MedGemma V6.0.3.1",
        "selectionMode": "curated_best_per_scenario",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract(zip_path: Path, target: Path) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(target)


def import_backend_modules(backend_root: Path):
    sys.path.insert(0, str(backend_root))
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
    from app.evaluation_injection.response_contract import (
        RESPONSE_CONTRACT_VERSION,
        validate_model_response_v6_0_3,
    )
    from app.evaluation_injection.response_validator import (
        validate_grounded_response,
    )

    return {
        "load_answer_key": load_scenario_answer_key,
        "adapt": adapt_v6_0_3_to_legacy_validator,
        "score": score_etiology_context_response,
        "contract_version": RESPONSE_CONTRACT_VERSION,
        "validate_contract": validate_model_response_v6_0_3,
        "validate": validate_grounded_response,
    }


def index_results(root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for path in root.rglob("*.json"):
        try:
            value = load_json(path)
        except Exception:
            continue
        model = str(value.get("model") or "")
        scenario = str(value.get("scenarioId") or "")
        if model and scenario and value.get("modelResponse"):
            indexed[(model, scenario)] = value
    return indexed


def read_selection_manifest(path: Path) -> dict[str, Any]:
    value = load_json(path)
    scenarios = value.get("scenarios")
    if not isinstance(scenarios, dict):
        raise ValueError("Selection manifest must contain a scenarios object.")
    missing = sorted(set(SCENARIOS) - set(scenarios))
    if missing:
        raise ValueError(f"Selection manifest is missing scenarios: {missing}")
    return value


def model_for_profile(
    profile: str,
    scenario: str,
    selection: dict[str, Any],
) -> str:
    if profile != "curated":
        return str(MODEL_PROFILES[profile]["model"])
    entry = (selection.get("scenarios") or {}).get(scenario) or {}
    model = str(entry.get("model") or "")
    if model not in {
        "google/medgemma-27b-it",
        "google/medgemma-27b-text-it",
    }:
        raise ValueError(
            f"Curated selection has an unsupported model for {scenario}: {model!r}"
        )
    return model


def clinical_interpretation(
    response: dict[str, Any],
    diagnostic: dict[str, Any],
) -> dict[str, Any]:
    diagnosis = str((diagnostic.get("diagnosis") or {}).get("display") or "")
    return {
        "headline": diagnosis,
        "statusLabel": "Clinical interpretation",
        "displayPolicy": "four_section_clinical_narrative",
        "episodeNarrative": response["episodeSummary"],
        "etiologyContextNarrative": response[
            "mostLikelyEtiologyAndClinicalContext"
        ],
        "rootCauseNarrative": response[
            "mostLikelyEtiologyAndClinicalContext"
        ],
        "possibleContributors": response["contributingFactors"],
        "importantFindings": response["contributingFactors"],
        "importantLimitations": response["materialEtiologicUncertainty"],
        "materialEtiologicUncertainty": response[
            "materialEtiologicUncertainty"
        ],
        "arrhythmiaNarrative": "",
        "morphologyNarrative": "",
        "currentSituationNarrative": "",
        "currentSituation": {"narrative": ""},
        "recommendedNextChecks": [],
        "recommendedActionsRequired": False,
    }


def install_one(
    *,
    profile: str,
    scenario: str,
    model: str,
    result: dict[str, Any],
    item: dict[str, Any],
    item_root: Path,
    destination: Path,
    selection: dict[str, Any],
    modules: dict[str, Any],
    response_set_id: str,
    validation_version: str,
    run_number: int,
    input_zip_sha256: str,
    results_zip_sha256: str,
) -> dict[str, Any]:
    expected_fingerprint = str(item.get("promptFingerprint") or "")
    actual_fingerprint = str(result.get("promptFingerprint") or "")
    if not expected_fingerprint or actual_fingerprint != expected_fingerprint:
        raise RuntimeError(
            f"Prompt fingerprint mismatch for {profile}/{scenario}: "
            f"{actual_fingerprint!r} != {expected_fingerprint!r}"
        )
    if int(result.get("runNumber") or 0) != run_number:
        raise RuntimeError(
            f"Unexpected run number for {profile}/{scenario}: "
            f"{result.get('runNumber')!r}"
        )
    if not result.get("generationSucceeded") or not result.get("validContract"):
        raise RuntimeError(f"Result is not a valid completed generation: {profile}/{scenario}")

    response = modules["validate_contract"](dict(result["modelResponse"]))
    legacy = modules["adapt"](response)
    diagnostic = load_json(item_root / "diagnostic_event.json")
    validator_evidence = load_json(item_root / "validator_evidence.json")
    validation = modules["validate"](
        response=legacy,
        diagnostic_event=diagnostic,
        supplied_evidence=validator_evidence,
    )
    if not bool(validation.get("validatorPassed", validation.get("accepted"))):
        raise RuntimeError(f"Validator did not pass for {profile}/{scenario}: {validation}")
    if validation.get("unsupportedFacts") or validation.get("contradictions"):
        raise RuntimeError(f"Grounding failure for {profile}/{scenario}: {validation}")

    answer_key = modules["load_answer_key"](
        scenario,
        allow_legacy_fallback=False,
    )
    score = modules["score"](
        episode_id=scenario,
        model_response=legacy,
        diagnostic_event=diagnostic,
        validation=validation,
        answer_key=answer_key,
    )

    profile_config = MODEL_PROFILES[profile]
    model_slug = str(profile_config["slug"])
    source_episode_id = str(result.get("episodeId") or scenario)
    interpretation = clinical_interpretation(response, diagnostic)
    created_at = utc_now()

    installation_metadata = {
        "schemaVersion": "kgen-precomputed-installation-v2",
        "createdAtUtc": created_at,
        "responseSetId": response_set_id,
        "profile": profile,
        "selectionMode": profile_config["selectionMode"],
        "scenarioId": scenario,
        "sourceModel": model,
        "modelSlug": model_slug,
        "runNumber": run_number,
        "promptFingerprint": expected_fingerprint,
        "sourcePromptVersion": "episode-pack-phase6-v6.0.3",
        "responseContractVersion": modules["contract_version"],
        "validationVersion": validation_version,
        "inputZipSha256": input_zip_sha256,
        "resultsZipSha256": results_zip_sha256,
        "validatorPassed": True,
        "unsupportedFactCount": 0,
        "contradictionCount": 0,
        "score": score.get("total"),
        "sourceGeneratedOffline": True,
    }

    model_metadata = {
        "provider": "lightning-ai-studio",
        "name": model,
        "modelId": model,
        "modelKey": result.get("modelKey"),
        "loaderType": result.get("loaderType"),
        "quantization": result.get("quantization"),
        "gpuName": result.get("gpuName"),
        "promptFingerprint": expected_fingerprint,
        "responseContractVersion": modules["contract_version"],
        "sourceRun": run_number,
        "generatedOffline": True,
    }

    cardinal = {
        "schemaVersion": "grounded-cardinal-response-v4",
        "status": "complete",
        "createdAt": created_at,
        "mode": "precomputed_response_installation",
        "scenarioId": scenario,
        "episodeId": source_episode_id,
        "incidentId": str((diagnostic.get("incidentId") or "")),
        "source": "validated_lightning_response_v6_0_3_1",
        "diagnosticEvent": diagnostic,
        "evidenceFingerprint": expected_fingerprint,
        "model": model_metadata,
        "sourceModelResponse": response,
        "modelResponse": response,
        "displayModelResponse": response,
        "validatedModelResponse": response,
        "legacyValidatorResponse": legacy,
        "validation": validation,
        "reliability": {
            "attemptCount": result.get("generationAttempts") or 1,
            "firstAttemptAccepted": True,
            "finalAttemptAccepted": True,
            "contractNormalized": bool(result.get("contractNormalized")),
            "normalizationChanges": result.get("normalizationChanges") or [],
            "contradictionCount": 0,
            "unsupportedFactCount": 0,
        },
        "generationAttempted": True,
        "liveInference": False,
        "generatedOffline": True,
        "installationMetadata": installation_metadata,
    }

    widget = {
        "schemaVersion": "slm-widget-result-v4",
        "createdAt": created_at,
        "scenarioId": scenario,
        "episodeId": source_episode_id,
        "incidentId": diagnostic.get("incidentId"),
        "status": "complete",
        "validationStatus": validation.get("groundingStatus")
        or validation.get("status"),
        "displayPolicy": "always_show_model_response",
        "clinicalResponseLabel": "MedGemma Clinical Context",
        "model": model_metadata,
        "modelResponse": response,
        "displayModelResponse": response,
        "interpretation": interpretation,
        "responseValidation": validation,
        "benchmark": {
            "score": score.get("total"),
            "grade": score.get("grade"),
            "safetyPass": score.get("safetyPass"),
            "overallPass": score.get("overallPass"),
        },
        "generationAttempted": False,
        "liveInference": False,
        "generatedOffline": True,
    }

    benchmark = {
        **score,
        "schemaVersion": "benchmark-result-v4",
        "scenarioId": scenario,
        "sourceModel": model,
        "sourceRun": run_number,
        "responseSetId": response_set_id,
        "profile": profile,
        "displayInClinicalWidget": True,
        "generationAttempted": False,
        "liveInference": False,
        "validContract": True,
        "modelResponse": response,
        "normalizedModelResponse": response,
        "responseValidation": validation,
    }

    run_summary = {
        "schemaVersion": "precomputed-run-summary-v2",
        "createdAtUtc": created_at,
        "scenarioId": scenario,
        "episodeId": source_episode_id,
        "runNumber": run_number,
        "profile": profile,
        "responseSetId": response_set_id,
        "selectionMode": profile_config["selectionMode"],
        "sourceModel": model,
        "promptFingerprint": expected_fingerprint,
        "validContract": True,
        "validatorPassed": True,
        "strictlyAccepted": bool(validation.get("accepted")),
        "displayableWithReview": bool(validation.get("displayableWithReview")),
        "unsupportedFactCount": 0,
        "contradictionCount": 0,
        "score": score.get("total"),
        "sourceLatencySeconds": result.get("latencySeconds"),
        "sourceInputTokens": result.get("inputTokens"),
        "sourceOutputTokens": result.get("outputTokens"),
    }

    response_selection = {
        "schemaVersion": "kgen-precomputed-scenario-selection-v1",
        "responseSetId": response_set_id,
        "profile": profile,
        "selectionMode": profile_config["selectionMode"],
        "scenarioId": scenario,
        "selectedModel": model,
        "selectionManifestId": selection.get("responseSetId"),
    }

    destination.mkdir(parents=True, exist_ok=True)
    write_json(destination / "source_lightning_response.json", result)
    write_json(destination / "response_selection.json", response_selection)
    write_json(destination / "cardinal_model_response.json", cardinal)
    write_json(destination / "slm_widget_result_v4.json", widget)
    write_json(destination / "grounding_validation_v4.json", validation)
    write_json(destination / "evaluation_score.json", benchmark)
    write_json(destination / "benchmark_result_v4.json", benchmark)
    write_json(destination / "diagnostic_event.json", diagnostic)
    write_json(destination / "validator_evidence.json", validator_evidence)
    write_json(destination / "run_summary.json", run_summary)
    write_json(destination / "installation_metadata.json", installation_metadata)

    return installation_metadata


def parse_profiles(raw: str) -> list[str]:
    normalized = raw.strip().lower()
    if normalized == "all":
        return ["medgemma-27b-it", "medgemma-27b-text-it", "curated"]
    values = [value.strip() for value in normalized.split(",") if value.strip()]
    invalid = [value for value in values if value not in MODEL_PROFILES]
    if invalid:
        raise ValueError(f"Unsupported profiles: {invalid}")
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-root", required=True)
    parser.add_argument("--input-zip", required=True)
    parser.add_argument("--results-zip", required=True)
    parser.add_argument("--selection-manifest", required=True)
    parser.add_argument(
        "--output-folder",
        default="medgemma-v6-0-3-1-dual",
    )
    parser.add_argument("--profiles", default="all")
    parser.add_argument("--run-number", type=int, default=2)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    backend_root = Path(args.backend_root).resolve()
    input_zip = Path(args.input_zip).resolve()
    results_zip = Path(args.results_zip).resolve()
    selection_path = Path(args.selection_manifest).resolve()
    if not (backend_root / "app" / "evaluation_injection").is_dir():
        raise FileNotFoundError(f"Invalid backend root: {backend_root}")
    for path in (input_zip, results_zip, selection_path):
        if not path.exists():
            raise FileNotFoundError(path)

    modules = import_backend_modules(backend_root)
    selection = read_selection_manifest(selection_path)
    profiles = parse_profiles(args.profiles)
    response_set_id = "medgemma-dual-v6-0-3-1"
    validation_version = "v6.0.3.1"
    output_root = (
        backend_root
        / "data"
        / "colab_model_benchmark"
        / "evaluated"
        / args.output_folder
    )

    if output_root.exists() and args.overwrite:
        archive = output_root.parent / "archive" / (
            output_root.name + "-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(output_root), str(archive))
    elif output_root.exists():
        raise FileExistsError(
            f"Output already exists: {output_root}. Use --overwrite to archive and replace it."
        )

    with tempfile.TemporaryDirectory() as input_temp, tempfile.TemporaryDirectory() as result_temp:
        input_root = Path(input_temp)
        result_root = Path(result_temp)
        extract(input_zip, input_root)
        extract(results_zip, result_root)
        manifest = load_json(input_root / "manifest.json")
        if manifest.get("promptVersion") != "episode-pack-phase6-v6.0.3":
            raise RuntimeError(f"Unexpected prompt version: {manifest.get('promptVersion')!r}")
        if manifest.get("responseContractVersion") != modules["contract_version"]:
            raise RuntimeError(
                "Response contract mismatch: "
                f"{manifest.get('responseContractVersion')!r} != {modules['contract_version']!r}"
            )
        items = {
            str(item.get("scenarioId")): item
            for item in manifest.get("items") or []
            if isinstance(item, dict) and item.get("scenarioId")
        }
        if set(items) != set(SCENARIOS):
            raise RuntimeError(f"Input ZIP scenario mismatch: {sorted(items)}")
        indexed = index_results(result_root)

        installed: list[dict[str, Any]] = []
        for profile in profiles:
            profile_config = MODEL_PROFILES[profile]
            slug = str(profile_config["slug"])
            for scenario in SCENARIOS:
                model = model_for_profile(profile, scenario, selection)
                result = indexed.get((model, scenario))
                if result is None:
                    raise RuntimeError(f"Missing result for model={model}; scenario={scenario}")
                destination = output_root / slug / scenario / f"run-{args.run_number}"
                metadata = install_one(
                    profile=profile,
                    scenario=scenario,
                    model=model,
                    result=result,
                    item=items[scenario],
                    item_root=input_root / "items" / scenario,
                    destination=destination,
                    selection=selection,
                    modules=modules,
                    response_set_id=response_set_id,
                    validation_version=validation_version,
                    run_number=args.run_number,
                    input_zip_sha256=file_sha256(input_zip),
                    results_zip_sha256=file_sha256(results_zip),
                )
                installed.append(metadata)

    response_set_manifest = {
        "schemaVersion": "kgen-precomputed-response-set-v2",
        "createdAtUtc": utc_now(),
        "responseSetId": response_set_id,
        "sourceRun": args.run_number,
        "sourcePromptVersion": "episode-pack-phase6-v6.0.3",
        "responseContractVersion": modules["contract_version"],
        "validationVersion": validation_version,
        "scenarioCount": len(SCENARIOS),
        "installedProfiles": profiles,
        "profiles": {
            profile: {
                "modelSlug": MODEL_PROFILES[profile]["slug"],
                "modelName": MODEL_PROFILES[profile]["name"],
                "selectionMode": MODEL_PROFILES[profile]["selectionMode"],
                "scenarioCount": len(SCENARIOS),
            }
            for profile in profiles
        },
    }
    write_json(output_root / "response_set_manifest.json", response_set_manifest)
    write_json(
        output_root / "installation_report.json",
        {
            "schemaVersion": "kgen-precomputed-installation-report-v2",
            "createdAtUtc": utc_now(),
            "outputRoot": str(output_root),
            "responseSet": response_set_manifest,
            "installedArtifactCount": len(installed),
            "installed": installed,
        },
    )

    print(
        json.dumps(
            {
                "outputRoot": str(output_root),
                "profiles": profiles,
                "scenarioCount": len(SCENARIOS),
                "installedArtifactCount": len(installed),
                "allValidatorPassed": all(item["validatorPassed"] for item in installed),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
