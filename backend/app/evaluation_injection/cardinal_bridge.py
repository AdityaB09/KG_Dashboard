from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.evaluation_injection.answer_key_loader import (
    load_scenario_answer_key,
)
from app.evaluation_injection.evidence_normalizer import (
    normalize_scenario_evidence,
    rebuild_from_saved_input,
)
from app.evaluation_injection.evidence_consistency import (
    apply_evidence_consistency_preflight,
    evidence_invalid_validation,
)
from app.evaluation_injection.etiology_context_scorer import (
    score_etiology_context_response,
)
from app.evaluation_injection.grounded_cardinal_client import (
    call_grounded_cardinal_model,
)
from app.evaluation_injection.grounded_prompt_builder import (
    build_grounded_messages,
)
from app.evaluation_injection.model_clinical_evidence import (
    build_model_clinical_evidence,
    presentation_narrative_from_legacy,
    sanitize_legacy_model_output,
)
from app.evaluation_injection.response_validator import (
    validate_grounded_response,
)
from app.evaluation_injection.episode_pack_scope import (
    build_episode_pack_only_evidence,
    sanitize_complete_episode_pack,
)
from app.evaluation_injection.review_safe_normalizer import (
    normalize_reviewable_response,
)
from app.evaluation_injection.medical_validator_adjudicator import (
    public_medical_review,
    run_medical_validator_review,
)
from app.evaluation_injection.precomputed_response_repository import (
    PrecomputedResponseError,
    load_precomputed_response,
    precomputed_artifact_set_id,
    precomputed_demo_delay_seconds,
    precomputed_demo_enabled,
    precomputed_demo_required,
    precomputed_model_name,
    precomputed_profile,
    precomputed_selection_mode,
)


class CardinalBridgeError(RuntimeError):
    pass


EPISODE_PACK_MODE = "episode_pack_only"


def _evaluation_context_mode() -> str:
    """
    Evaluation injection is episode-pack-only by design.

    Environment values are still read for diagnostics, but legacy Oracle-bound
    modes are not allowed to change the evidence source for this bridge.
    """
    configured = (
        os.getenv(
            "EVALUATION_CONTEXT_MODE",
            os.getenv(
                "SLM_PROMPT_MODE",
                EPISODE_PACK_MODE,
            ),
        )
        .strip()
        .lower()
    )

    if configured and configured != EPISODE_PACK_MODE:
        print(
            "[KGEN EPISODE PACK MODE FORCED]",
            {
                "configuredMode": configured,
                "effectiveMode": EPISODE_PACK_MODE,
            },
            flush=True,
        )

    return EPISODE_PACK_MODE


def _scenario_dataset_root() -> Path:
    root = Path(
        settings.EVALUATION_INJECTION_DATASET_ROOT
    )

    if not root.is_absolute():
        root = (
            Path(__file__)
            .resolve()
            .parents[2]
            / root
        ).resolve()

    return root


def _load_complete_episode_pack(
    scenario_id: str,
) -> dict[str, Any]:
    path = (
        _scenario_dataset_root()
        / "episodes"
        / f"{scenario_id}.json"
    )

    if not path.exists():
        raise FileNotFoundError(
            "Complete episode package not found: "
            + str(path)
        )

    raw = json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(raw, dict):
        raise CardinalBridgeError(
            f"Scenario package is not a JSON object: {path}"
        )

    return sanitize_complete_episode_pack(raw)


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _attach_saved_capture_provenance(
    *,
    evidence_bundle: dict[str, Any],
    source_episode_dir: Path,
) -> dict[str, Any]:
    """Attach metadata-owned segment provenance and windowed Phase 6 output."""
    output = dict(evidence_bundle)
    metadata = _read_json_object(source_episode_dir / "metadata.json")

    source_segments = metadata.get("sourceSegments")
    if isinstance(source_segments, list):
        output["sourceSegments"] = source_segments

    for key in (
        "captureStartSeconds",
        "captureEndSeconds",
        "eventStartOffsetSeconds",
        "eventEndOffsetSeconds",
        "durationSeconds",
    ):
        if metadata.get(key) is not None:
            output[key] = metadata.get(key)

    windowed = _read_json_object(
        source_episode_dir / "analysis_windowed.json"
    )
    if not windowed:
        analysis = _read_json_object(
            source_episode_dir / "analysis.json"
        )
        candidate = analysis.get("windowedAnalysis")
        if isinstance(candidate, dict):
            windowed = candidate

    if windowed:
        deterministic = dict(output.get("deterministicAnalysis") or {})
        deterministic["windowedAnalysis"] = windowed
        deterministic["phase6WindowedAnalysis"] = windowed
        output["deterministicAnalysis"] = deterministic
        output["phase6WindowedAnalysis"] = windowed

    return output


def _capture_evidence_from_record(
    record: dict[str, Any],
) -> dict[str, Any]:
    diagnostic_event = (
        record.get("diagnosticEvent")
        or {}
    )
    evidence = (
        record.get("evidenceBundle")
        or record.get("suppliedEvidence")
        or {}
    )
    capture = (
        diagnostic_event.get("capture")
        or (
            evidence.get("captureContext")
            or {}
        ).get("capture")
        or evidence.get("episode")
        or {}
    )
    detector = (
        diagnostic_event.get("detector")
        or (
            evidence.get("captureContext")
            or {}
        ).get("detector")
        or evidence.get("detector")
        or {}
    )

    return {
        "captureDurationSeconds": (
            capture.get("durationSeconds")
            or capture.get("captureSeconds")
        ),
        "preSecondsCaptured": (
            capture.get("preSeconds")
            or capture.get("preEventSeconds")
        ),
        "eventDurationSeconds": (
            capture.get("eventSeconds")
        ),
        "postSecondsCaptured": (
            capture.get("postSeconds")
            or capture.get("postEventSeconds")
        ),
        "captureCompleteness": {
            "captureComplete": (
                capture.get("complete")
                if capture.get("complete") is not None
                else capture.get("captureComplete")
            )
        },
        "detectorRuleId": detector.get("ruleId"),
        "detectorRateBpm": (
            detector.get("estimatedRateBpm")
        ),
        "triggerLatencySeconds": (
            detector.get("triggerLatencySeconds")
        ),
        "referenceOnsetOffsetSeconds": (
            detector.get("referenceOnsetOffsetSeconds")
        ),
        "detectedTriggerOffsetSeconds": (
            detector.get("detectedTriggerOffsetSeconds")
        ),
        "sampleRateHz": (
            capture.get("sampleRateHz")
            or capture.get("sampleRate")
        ),
        "leads": capture.get("leads") or [],
        "demoRunId": capture.get("demoRunId"),
    }


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def _fingerprint(value: dict[str, Any]) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _selected_model(model_metadata: dict[str, Any] | None) -> str | None:
    if not isinstance(model_metadata, dict):
        return None

    for key in ("name", "model", "modelName"):
        value = model_metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return None


def _final_cardinal_response(
    *,
    model_response: dict[str, Any],
    diagnostic_event: dict[str, Any],
) -> dict[str, Any]:
    """Build a backward-compatible display record from sanitized model text."""
    cleaned = sanitize_legacy_model_output(model_response)
    presentation = presentation_narrative_from_legacy(cleaned)

    return {
        "episodeSummary": presentation["episodeSummary"],
        "mostLikelyEtiologyAndClinicalContext": presentation[
            "mostLikelyEtiologyAndClinicalContext"
        ],
        "materialEtiologicUncertainty": presentation[
            "materialEtiologicUncertainty"
        ],
        "contributingFactors": presentation["contributingFactors"],

        # Legacy compatibility fields retained for the current validator,
        # benchmark, stored-response readers, and older clients.
        "detectedEpisodeContext": cleaned["detectedEpisodeContext"],
        "rhythmInterpretation": "",
        "clinicalContext": presentation[
            "mostLikelyEtiologyAndClinicalContext"
        ],
        "mostLikelyEtiology": presentation[
            "mostLikelyEtiologyAndClinicalContext"
        ],
        "uncertaintyAndMissingData": presentation[
            "materialEtiologicUncertainty"
        ],
        "recommendedImmediateActions": [],
    }


def _validation_summary(
    validation: dict[str, Any],
) -> dict[str, Any]:
    status = str(
        validation.get("groundingStatus")
        or validation.get("status")
        or "unknown"
    )
    hard_pass = _validation_hard_pass(validation)
    return {
        "status": status,
        "strictlyAccepted": bool(validation.get("accepted")),
        "hardAccepted": hard_pass,
        "displayableWithReview": bool(validation.get("displayableWithReview")),
        "validatorPassed": hard_pass,
        "hardErrorCount": len(validation.get("hardErrors") or []),
        "qualityErrorCount": len(validation.get("qualityErrors") or []),
        "contradictionCount": len(validation.get("contradictions") or []),
        "unsupportedFactCount": len(validation.get("unsupportedFacts") or []),
        "errors": list(validation.get("errors") or []),
        "hardErrors": list(validation.get("hardErrors") or []),
        "qualityErrors": list(validation.get("qualityErrors") or []),
        "contradictions": list(validation.get("contradictions") or []),
        "unsupportedFacts": list(validation.get("unsupportedFacts") or []),
    }


def _widget_interpretation(
    *,
    cardinal: dict[str, Any],
    diagnostic_event: dict[str, Any],
    validation: dict[str, Any],
) -> dict[str, Any]:
    """Return only the four clinical presentation blocks."""
    diagnosis = str((diagnostic_event.get("diagnosis") or {}).get("display") or "")
    narrative = presentation_narrative_from_legacy(cardinal)
    return {
        "headline": diagnosis,
        "statusLabel": "Clinical interpretation",
        "displayPolicy": "four_section_clinical_narrative",
        "episodeNarrative": narrative["episodeSummary"],
        "etiologyContextNarrative": narrative[
            "mostLikelyEtiologyAndClinicalContext"
        ],
        "rootCauseNarrative": narrative[
            "mostLikelyEtiologyAndClinicalContext"
        ],
        "possibleContributors": narrative["contributingFactors"],
        "importantFindings": narrative["contributingFactors"],
        "importantLimitations": narrative["materialEtiologicUncertainty"],
        "materialEtiologicUncertainty": narrative[
            "materialEtiologicUncertainty"
        ],

        # Empty compatibility values prevent older frontends from displaying
        # repetitive or technical context blocks.
        "arrhythmiaNarrative": "",
        "morphologyNarrative": "",
        "currentSituationNarrative": "",
        "currentSituation": {"narrative": ""},
        "recommendedNextChecks": [],
        "recommendedActionsRequired": False,
        "responseValidation": validation,
        "validationSummary": _validation_summary(validation),
    }


def _phase7_response_path(incident_id: str) -> Path:
    return (
        Path(settings.INCIDENT_STORAGE_PATH)
        / "phase7"
        / incident_id
        / "slm_response.json"
    )


def _attach_to_phase7_storage(
    *,
    incident_id: str,
    diagnostic_event: dict[str, Any],
    cardinal: dict[str, Any],
    validation: dict[str, Any],
    model_metadata: dict[str, Any],
    response_file: str,
    response_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = _phase7_response_path(incident_id)
    stored: dict[str, Any] = {}
    existing_payload: dict[str, Any] = {}
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            stored = {}
        content = stored.get("content")
        if isinstance(content, dict):
            existing_payload = content
        elif isinstance(content, str):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    existing_payload = parsed
            except json.JSONDecodeError:
                pass
    summary = _validation_summary(validation)
    payload = {
        **existing_payload,
        "code": (
            "generated_validated" if summary["strictlyAccepted"]
            else "generated_with_review" if summary["displayableWithReview"]
            else "generated_validation_failed"
        ),
        "description": "SLM response generated; validation statistics are stored separately.",
        "displayPolicy": "always_show_model_response",
        "diagnosticEvent": diagnostic_event,
        "cardinalEvaluation": cardinal,
        "responseValidation": validation,
        "validationSummary": summary,
        "widgetInterpretation": _widget_interpretation(
            cardinal=cardinal,
            diagnostic_event=diagnostic_event,
            validation=validation,
        ),
        "precomputedResponse": response_provenance,
        # Keep offline/live provenance in backend audit metadata, but do not
        # expose a technical "precomputed" label in the clinical widget.
        "responseProvenanceLabel": None,
        "clinicalResponseLabel": "MedGemma Clinical Context",
        "liveInference": (
            response_provenance.get("liveInference")
            if response_provenance
            else True
        ),
    }
    stored.update({
        "schemaVersion": stored.get("schemaVersion") or "phase7-slm-response-v1",
        "incidentId": incident_id,
        "status": "complete",
        "validationStatus": summary["status"],
        "model": model_metadata,
        "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        "cardinalEvaluation": {
            "status": summary["status"],
            "responseFile": response_file,
            "diagnosticSource": diagnostic_event.get("source") or {},
            "model": model_metadata,
            "recommendedActionsRequired": False,
            "displayPolicy": "always_show_model_response",
            "precomputedResponse": response_provenance,
            "liveInference": (
                response_provenance.get("liveInference")
                if response_provenance
                else True
            ),
        },
    })
    _atomic_json(path, stored)
    return stored

def _validation_is_displayable(
    validation: dict[str, Any],
) -> bool:
    return bool(
        _validation_hard_pass(validation)
        and (
            validation.get("accepted")
            or validation.get("displayableWithReview")
        )
    )


def _reliability_metadata(
    *,
    attempt_count: int,
    first_validation: dict[str, Any],
    final_validation: dict[str, Any],
) -> dict[str, Any]:
    first_errors = list(
        first_validation.get("errors") or []
    )

    return {
        "attemptCount": attempt_count,

        # "Accepted" here means safe to display, including accepted_with_review.
        "firstAttemptAccepted": _validation_is_displayable(
            first_validation
        ),
        "firstAttemptStrictlyAccepted": bool(
            first_validation.get("accepted")
        ),
        "firstAttemptDisplayableWithReview": bool(
            first_validation.get(
                "displayableWithReview"
            )
        ),

        "firstAttemptErrors": first_errors,

        "finalAttemptAccepted": _validation_is_displayable(
            final_validation
        ),
        "finalAttemptStrictlyAccepted": bool(
            final_validation.get("accepted")
        ),
        "finalAttemptDisplayableWithReview": bool(
            final_validation.get(
                "displayableWithReview"
            )
        ),

        "retryReasonCount": (
            len(first_errors)
            if attempt_count > 1
            else 0
        ),
        "contradictionCount": len(
            final_validation.get(
                "contradictions"
            )
            or []
        ),
        "unsupportedFactCount": len(
            final_validation.get(
                "unsupportedFacts"
            )
            or []
        ),
        "evidenceCoverageCount": int(
            final_validation.get(
                "evidenceCoverageCount"
            )
            or 0
        ),
        "evidenceCoverageRequired": int(
            final_validation.get(
                "evidenceCoverageRequired"
            )
            or 0
        ),
    }




def _phase7_evidence_for_scope(
    incident_id: str,
    phase7_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    supplied = (phase7_result or {}).get("evidence")
    if isinstance(supplied, dict):
        return supplied

    path = (
        Path(settings.INCIDENT_STORAGE_PATH)
        / "phase7"
        / incident_id
        / "evidence_package.json"
    )
    if not path.exists():
        return {}

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_v4_shadow_artifacts(
    *,
    output_dir: Path,
    evidence_bundle: dict[str, Any],
) -> None:
    if evidence_bundle.get("schemaVersion") != "slm-evidence-envelope-v4":
        return

    _atomic_json(output_dir / "slm_evidence_v4.json", evidence_bundle)
    contract = evidence_bundle.get("validatorContract") or {}
    if isinstance(contract, dict):
        _atomic_json(output_dir / "validator_contract_v4.json", contract)
    _atomic_json(
        output_dir / "measurement_conflicts.json",
        {
            "schemaVersion": "measurement-conflicts-v4",
            "episodeId": evidence_bundle.get("episode", {}).get("episodeId"),
            "items": evidence_bundle.get("measurementConflicts") or [],
        },
    )
    _atomic_json(
        output_dir / "patient_linkage.json",
        {
            "schemaVersion": "patient-linkage-v4",
            **(evidence_bundle.get("patientLinkage") or {}),
        },
    )


def _widget_dto_v4(
    *,
    scenario_id: str,
    episode_id: str,
    incident_id: str,
    cardinal: dict[str, Any],
    diagnostic_event: dict[str, Any],
    validation: dict[str, Any],
    score: dict[str, Any],
    evidence_bundle: dict[str, Any],
    model_metadata: dict[str, Any],
) -> dict[str, Any]:
    narrative = presentation_narrative_from_legacy(cardinal)
    cleaned = sanitize_legacy_model_output(cardinal)
    return {
        "schemaVersion": "slm-widget-result-v4",
        "scenarioId": scenario_id,
        "episodeId": episode_id,
        "incidentId": incident_id,
        "status": "complete",
        "validationStatus": validation.get("groundingStatus") or validation.get("status"),
        "displayPolicy": "four_section_clinical_narrative",
        "validationSummary": _validation_summary(validation),
        "headline": str((diagnostic_event.get("diagnosis") or {}).get("display") or ""),
        "narrative": narrative,
        "legacyCompatibility": {
            "episodeSummary": cleaned["episodeSummary"],
            "detectedEpisodeContext": cleaned["detectedEpisodeContext"],
            "clinicalContext": cleaned["mostLikelyEtiology"],
            "mostLikelyEtiology": cleaned["mostLikelyEtiology"],
            "contributingFactors": cleaned["contributingFactors"],
            "uncertaintyAndMissingData": cleaned["uncertaintyAndMissingData"],
        },
        "widgetInterpretation": _widget_interpretation(
            cardinal=cardinal,
            diagnostic_event=diagnostic_event,
            validation=validation,
        ),
        "grounding": score.get("grounding") or {},
        "benchmark": score.get("benchmark") or {},
        "evaluationStatistics": {
            "scenarioScore": score.get("total"),
            "overallPass": score.get("overallPass"),
            "safetyPass": score.get("safetyPass"),
            "attemptCount": score.get("attemptCount"),
            "generationLatencySeconds": model_metadata.get("elapsedSeconds"),
            "rawResponseDisplayed": True,
        },
        "clinicalPromptMode": evidence_bundle.get("clinicalPromptMode"),
        "model": model_metadata,
        "manualReviewRequired": bool(score.get("manualClinicalReviewRequired")),
        "medicalAdjudication": public_medical_review(validation.get("medicalAdjudication")),
        "medicalReviewRecommended": bool(validation.get("medicalReviewRecommended")),
    }


def _model_owned_from_cardinal(
    cardinal: dict[str, Any],
) -> dict[str, Any]:
    """Convert any stored response shape to the legacy five validator fields."""
    return sanitize_legacy_model_output(
        {
            "episodeSummary": cardinal.get("episodeSummary"),
            "detectedEpisodeContext": (
                cardinal.get("detectedEpisodeContext")
                or cardinal.get("episodeSummary")
            ),
            "mostLikelyEtiologyAndClinicalContext": (
                cardinal.get("mostLikelyEtiologyAndClinicalContext")
                or cardinal.get("mostLikelyEtiology")
                or cardinal.get("clinicalContext")
            ),
            "contributingFactors": cardinal.get("contributingFactors") or [],
            "materialEtiologicUncertainty": (
                cardinal.get("materialEtiologicUncertainty")
                or cardinal.get("uncertaintyAndMissingData")
                or []
            ),
        }
    )


def _without_schema_version(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if key != "schemaVersion"
    }


async def _run_precomputed_demo_pipeline(
    *,
    scenario_id: str,
    episode_id: str,
    incident_id: str,
    output_dir: Path,
    diagnostic_event: dict[str, Any],
    evidence_bundle: dict[str, Any],
    consistency_review: dict[str, Any],
    update_phase7_storage: bool,
) -> dict[str, Any]:
    """Materialize one of the eight offline Lightning responses for a live demo run."""
    delay_seconds = precomputed_demo_delay_seconds()
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)

    artifacts = load_precomputed_response(
        scenario_id
    )

    source_cardinal_record = artifacts.cardinal
    source_display = (
        source_cardinal_record.get(
            "displayModelResponse"
        )
        or source_cardinal_record.get(
            "modelResponse"
        )
        or {}
    )
    source_validated = (
        source_cardinal_record.get(
            "validatedModelResponse"
        )
        or source_display
    )

    display_cardinal = _final_cardinal_response(
        model_response=_model_owned_from_cardinal(
            source_display
        ),
        diagnostic_event=diagnostic_event,
    )
    validated_cardinal = _final_cardinal_response(
        model_response=_model_owned_from_cardinal(
            source_validated
        ),
        diagnostic_event=diagnostic_event,
    )

    validation = (
        source_cardinal_record.get("validation")
        or _without_schema_version(
            artifacts.validation
        )
        or {
            "status": "accepted_with_review",
            "groundingStatus": "accepted_with_review",
            "accepted": False,
            "hardAccepted": True,
            "displayableWithReview": True,
            "validatorPassed": True,
            "hardErrors": [],
            "qualityErrors": [
                "Stored validation metadata was unavailable."
            ],
            "contradictions": [],
            "unsupportedFacts": [],
        }
    )

    source_model = (
        source_cardinal_record.get("model")
        or artifacts.widget.get("model")
        or {}
    )
    source_evidence_fingerprint = str(
        source_cardinal_record.get(
            "evidenceFingerprint"
        )
        or source_model.get(
            "promptFingerprint"
        )
        or ""
    )
    current_evidence_fingerprint = _fingerprint(
        evidence_bundle
    )

    provenance = {
        "schemaVersion": (
            "precomputed-lightning-response-linkage-v1"
        ),
        "mode": "precomputed_lightning_demo",
        "provider": "precomputed_lightning_artifact",
        "model": (
            source_model.get("name")
            or artifacts.source_model
            or precomputed_model_name()
        ),
        "lookupMode": "scenario_id",
        "scenarioId": scenario_id,
        "capturedEpisodeId": episode_id,
        "capturedIncidentId": incident_id,
        "sourceEpisodeId": (
            source_cardinal_record.get("episodeId")
            or artifacts.widget.get("episodeId")
        ),
        "sourceIncidentId": (
            source_cardinal_record.get("incidentId")
            or artifacts.widget.get("incidentId")
        ),
        "sourceEvidenceFingerprint": (
            source_evidence_fingerprint
            or None
        ),
        "currentEvidenceFingerprint": (
            current_evidence_fingerprint
        ),
        "exactEvidenceFingerprintMatch": bool(
            source_evidence_fingerprint
            and source_evidence_fingerprint
            == current_evidence_fingerprint
        ),
        "liveInference": False,
        "generatedOffline": True,
        "presentationDelaySeconds": delay_seconds,
        "sourceRun": artifacts.run_summary.get(
            "runNumber"
        ) or 1,
        "responseSetId": artifacts.response_set_id,
        "sourceArtifactSet": precomputed_artifact_set_id(),
        "activeProfile": artifacts.profile,
        "selectionMode": artifacts.selection_mode,
        "sourceModel": artifacts.source_model,
        "sourceWaveformRequirement": (
            "none_for_lookup"
        ),
        "supportedMappedSources": [
            "incart_episode",
            "api_range_episode",
        ],
    }

    model_metadata = {
        "provider": "precomputed_lightning_artifact",
        "name": (
            source_model.get("name")
            or artifacts.source_model
            or precomputed_model_name()
        ),
        "modelId": (
            source_model.get("name")
            or artifacts.source_model
            or precomputed_model_name()
        ),
        "structuredOutput": True,
        "schema": (
            source_model.get("schema")
            or "grounded-etiology-context-universal-v1"
        ),
        "precomputed": True,
        "liveInference": False,
        "lookupMode": "scenario_id",
        "precomputedProfile": precomputed_profile(),
        "selectionMode": precomputed_selection_mode(),
        "responseSetId": precomputed_artifact_set_id(),
        "sourceModel": artifacts.source_model,
        "sourcePromptFingerprint": (
            source_model.get("promptFingerprint")
        ),
        "sourceElapsedSeconds": (
            source_model.get("elapsedSeconds")
        ),
        "gpuName": source_model.get("gpuName"),
        "quantization": source_model.get(
            "quantization"
        ),
        "recommendedActionsRequired": False,
    }

    reliability = (
        source_cardinal_record.get("reliability")
        or {
            "attemptCount": (
                artifacts.run_summary.get(
                    "attemptCount"
                )
                or 1
            ),
            "firstAttemptAccepted": bool(
                artifacts.run_summary.get(
                    "strictlyAccepted"
                )
            ),
            "finalAttemptAccepted": bool(
                artifacts.run_summary.get(
                    "strictlyAccepted"
                )
                or artifacts.run_summary.get(
                    "displayableWithReview"
                )
            ),
            "contradictionCount": int(
                artifacts.run_summary.get(
                    "contradictionCount"
                )
                or 0
            ),
            "unsupportedFactCount": int(
                artifacts.run_summary.get(
                    "unsupportedFactCount"
                )
                or 0
            ),
            "evidenceCoverageCount": int(
                artifacts.run_summary.get(
                    "evidenceCoverageCount"
                )
                or 0
            ),
            "evidenceCoverageRequired": int(
                artifacts.run_summary.get(
                    "evidenceCoverageRequired"
                )
                or 0
            ),
        }
    )

    response_record = {
        **source_cardinal_record,
        "schemaVersion": "grounded-cardinal-response-v4",
        "status": "complete",
        "createdAt": _now_iso(),
        "mode": "evaluation_injection",
        "scenarioId": scenario_id,
        "episodeId": episode_id,
        "incidentId": incident_id,
        "source": (
            "precomputed_lightning_response_plus_current_capture"
        ),
        "diagnosticEvent": diagnostic_event,
        "evidenceFingerprint": current_evidence_fingerprint,
        "model": model_metadata,
        "modelResponse": display_cardinal,
        "displayModelResponse": display_cardinal,
        "validatedModelResponse": validated_cardinal,
        "validation": validation,
        "reliability": reliability,
        "precomputedResponse": provenance,
        "generationAttempted": False,
        "liveInference": False,
    }
    response_path = output_dir / "cardinal_model_response.json"
    _atomic_json(response_path, response_record)

    score = dict(
        artifacts.score
        or artifacts.benchmark
        or {}
    )
    score.update(
        {
            "schemaVersion": "benchmark-result-v4",
            "scenarioId": scenario_id,
            "capturedEpisodeId": episode_id,
            "incidentId": incident_id,
            "normalizedModelResponse": validated_cardinal,
            "displayModelResponse": display_cardinal,
            "cardinalResponseFile": response_path.name,
            "diagnosticEvent": diagnostic_event,
            "responseValidation": validation,
            "displayInClinicalWidget": True,
            "displayPolicy": "always_show_model_response",
            "clinicalPromptMode": evidence_bundle.get(
                "clinicalPromptMode"
            ),
            "evidenceConsistencyReview": consistency_review,
            "generationAttempted": False,
            "generationMode": "precomputed_lightning_demo",
            "liveInference": False,
            "validContract": True,
            "precomputedResponse": provenance,
            "reliability": reliability,
            **reliability,
        }
    )

    _atomic_json(
        output_dir / "evaluation_score.json",
        score,
    )
    _atomic_json(
        output_dir / "grounding_validation_v4.json",
        {
            "schemaVersion": "grounding-validation-v4",
            **validation,
            "precomputedResponse": provenance,
        },
    )
    _atomic_json(
        output_dir / "benchmark_result_v4.json",
        score,
    )
    _atomic_json(
        output_dir / "precomputed_response_linkage.json",
        provenance,
    )

    widget = _widget_dto_v4(
        scenario_id=scenario_id,
        episode_id=episode_id,
        incident_id=incident_id,
        cardinal=display_cardinal,
        diagnostic_event=diagnostic_event,
        validation=validation,
        score=score,
        evidence_bundle=evidence_bundle,
        model_metadata=model_metadata,
    )
    widget.update(
        {
            "status": "complete",
            "displayPolicy": (
                "always_show_model_response"
            ),
            "precomputedResponse": provenance,
            "responseProvenanceLabel": None,
            "clinicalResponseLabel": "MedGemma Clinical Context",
            "liveInference": False,
        }
    )
    _atomic_json(
        output_dir / "slm_widget_result_v4.json",
        widget,
    )

    phase7_slm_response: dict[str, Any] = {}
    if update_phase7_storage:
        phase7_slm_response = (
            _attach_to_phase7_storage(
                incident_id=incident_id,
                diagnostic_event=diagnostic_event,
                cardinal=display_cardinal,
                validation=validation,
                model_metadata=model_metadata,
                response_file=response_path.name,
                response_provenance=provenance,
            )
        )

    _atomic_json(
        output_dir / "status.json",
        {
            "schemaVersion": (
                "grounded-pipeline-status-v1"
            ),
            "status": "complete",
            "createdAt": _now_iso(),
            "scenarioId": scenario_id,
            "episodeId": episode_id,
            "incidentId": incident_id,
            "generationAttempted": False,
            "generationMode": (
                "precomputed_lightning_demo"
            ),
            "liveInference": False,
            "precomputedResponse": provenance,
        },
    )

    print(
        "[KGEN PRECOMPUTED MEDGEMMA RESPONSE ATTACHED]",
        {
            "scenarioId": scenario_id,
            "episodeId": episode_id,
            "incidentId": incident_id,
            "baseWaveformSource": (
                evidence_bundle.get(
                    "baseWaveformSource"
                )
            ),
            "validationStatus": (
                validation.get("groundingStatus")
                or validation.get("status")
            ),
            "benchmarkScore": score.get("total"),
            "liveInference": False,
        },
        flush=True,
    )

    return {
        "status": "complete",
        "validationStatus": (
            validation.get("groundingStatus")
            or validation.get("status")
        ),
        "source": (
            "precomputed_lightning_response_plus_current_capture"
        ),
        "evidenceSchemaVersion": evidence_bundle.get(
            "schemaVersion"
        ),
        "clinicalPromptMode": evidence_bundle.get(
            "clinicalPromptMode"
        ),
        "diagnosticEvent": diagnostic_event,
        "modelResponse": display_cardinal,
        "displayModelResponse": display_cardinal,
        "validatedModelResponse": validated_cardinal,
        "model": model_metadata,
        "validation": validation,
        "score": score,
        "reliability": reliability,
        "phase7SlmResponse": phase7_slm_response,
        "responseFile": response_path.name,
        "artifactDirectory": str(output_dir),
        "evidenceConsistencyReview": consistency_review,
        "generationAttempted": False,
        "liveInference": False,
        "validContract": True,
        "precomputedResponse": provenance,
    }


async def _run_grounded_pipeline(
    *,
    scenario_id: str,
    episode_id: str,
    incident_id: str,
    source_episode_dir: Path,
    diagnostic_event: dict[str, Any],
    evidence_bundle: dict[str, Any],
    model_override: str | None = None,
    artifact_dir: Path | None = None,
    update_phase7_storage: bool = True,
    phase7_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    phase7_scope_evidence = (
        phase7_evidence
        if isinstance(
            phase7_evidence,
            dict,
        )
        else _phase7_evidence_for_scope(
            incident_id
        )
    )

    context_mode = _evaluation_context_mode()

    if context_mode != EPISODE_PACK_MODE:
        raise CardinalBridgeError(
            "Evaluation injection must use episode_pack_only mode."
        )

    # Fail closed. Never fall back to an Oracle-bound or unscoped bundle.
    evidence_bundle = build_episode_pack_only_evidence(
        evidence_bundle,
        phase7_evidence=phase7_scope_evidence,
    )
    evidence_bundle = _attach_saved_capture_provenance(
        evidence_bundle=evidence_bundle,
        source_episode_dir=source_episode_dir,
    )

    # Repair deterministic normalization defects and reject internally
    # inconsistent evidence before any model call. This prevents evidence
    # defects from being counted as model contradictions.
    diagnostic_event, evidence_bundle, consistency_review = (
        apply_evidence_consistency_preflight(
            diagnostic_event=diagnostic_event,
            evidence_bundle=evidence_bundle,
        )
    )

    if (
        evidence_bundle.get("clinicalPromptMode")
        != EPISODE_PACK_MODE
    ):
        raise CardinalBridgeError(
            "Episode-pack scoping did not produce the required prompt mode."
        )

    oracle_context = (
        evidence_bundle.get("oracleContext")
        or {}
    )
    if (
        oracle_context.get("available") is not False
        or oracle_context.get("excludedByPolicy") is not True
    ):
        raise CardinalBridgeError(
            "Oracle FHIR clinical context was not excluded."
        )
    output_dir = artifact_dir or source_episode_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(
        output_dir / "evidence_consistency.json",
        consistency_review,
    )
    _write_v4_shadow_artifacts(
        output_dir=output_dir,
        evidence_bundle=evidence_bundle,
    )

    model_clinical_evidence = build_model_clinical_evidence(
        evidence_bundle=evidence_bundle,
    )
    _atomic_json(
        output_dir / "model_clinical_evidence.json",
        model_clinical_evidence,
    )

    input_record = {
        "schemaVersion": (
            "grounded-model-input-universal-v4"
            if evidence_bundle.get("schemaVersion") == "slm-evidence-envelope-v4"
            else "grounded-model-input-universal-v2"
        ),
        "createdAt": _now_iso(),
        "scenarioId": scenario_id,
        "episodeId": episode_id,
        "incidentId": incident_id,
        "diagnosticEvent": diagnostic_event,
        "evidenceBundle": evidence_bundle,
        "modelClinicalEvidence": model_clinical_evidence,
        "evidenceConsistencyReview": consistency_review,
        "recommendedActionsRequired": False,
        "modelOverride": model_override,
        "sourceEpisodeDirectory": str(source_episode_dir),
    }
    _atomic_json(output_dir / "grounded_model_input.json", input_record)
    _atomic_json(output_dir / "diagnostic_event.json", diagnostic_event)

    if consistency_review.get("status") == "evidence_invalid":
        validation = evidence_invalid_validation(
            consistency_review,
            diagnostic_event=diagnostic_event,
        )
        score = {
            "schemaVersion": "benchmark-result-v4",
            "episodeId": scenario_id,
            "status": "evidence_invalid",
            "groundingPass": False,
            "safetyPass": False,
            "overallPass": False,
            "benchmarkPass": None,
            "benchmarkDisposition": "not_scored_evidence_invalid",
            "overallDisposition": "evidence_invalid_before_generation",
            "generationAttempted": False,
            "validContract": False,
            "grounding": {
                "status": "evidence_invalid",
                "pass": False,
                "accepted": False,
                "displayableWithReview": False,
                "hardErrorCount": 0,
                "qualityErrorCount": 0,
            },
            "benchmark": {
                "score": None,
                "grade": "not_scored",
                "pass": None,
                "informationalOnly": True,
                "disposition": "not_scored_evidence_invalid",
            },
            "responseValidation": validation,
            "evidenceConsistencyReview": consistency_review,
        }
        _atomic_json(output_dir / "grounding_validation_v4.json", {
            "schemaVersion": "grounding-validation-v4",
            **validation,
        })
        _atomic_json(output_dir / "evaluation_score.json", score)
        _atomic_json(output_dir / "benchmark_result_v4.json", score)
        _atomic_json(output_dir / "status.json", {
            "schemaVersion": "grounded-pipeline-status-v1",
            "status": "evidence_invalid",
            "createdAt": _now_iso(),
            "scenarioId": scenario_id,
            "episodeId": episode_id,
            "incidentId": incident_id,
            "generationAttempted": False,
            "evidenceConsistencyReview": consistency_review,
        })
        print(
            "[KGEN EVIDENCE INVALID - MODEL NOT CALLED]",
            {
                "episodeId": episode_id,
                "scenarioId": scenario_id,
                "hardConflicts": consistency_review.get("hardConflicts") or [],
            },
            flush=True,
        )
        return {
            "status": "evidence_invalid",
            "validationStatus": "evidence_invalid",
            "evidenceSchemaVersion": evidence_bundle.get("schemaVersion"),
            "clinicalPromptMode": evidence_bundle.get("clinicalPromptMode"),
            "diagnosticEvent": diagnostic_event,
            "modelResponse": None,
            "displayModelResponse": None,
            "validatedModelResponse": None,
            "model": None,
            "validation": validation,
            "score": score,
            "reliability": {
                "attemptCount": 0,
                "firstAttemptAccepted": False,
                "finalAttemptAccepted": False,
                "contradictionCount": 0,
                "unsupportedFactCount": 0,
                "evidenceCoverageCount": 0,
                "evidenceCoverageRequired": 0,
            },
            "phase7SlmResponse": {},
            "responseFile": None,
            "artifactDirectory": str(output_dir),
            "evidenceConsistencyReview": consistency_review,
            "generationAttempted": False,
        }

    if precomputed_demo_enabled():
        try:
            return await _run_precomputed_demo_pipeline(
                scenario_id=scenario_id,
                episode_id=episode_id,
                incident_id=incident_id,
                output_dir=output_dir,
                diagnostic_event=diagnostic_event,
                evidence_bundle=evidence_bundle,
                consistency_review=consistency_review,
                update_phase7_storage=update_phase7_storage,
            )
        except PrecomputedResponseError as error:
            if precomputed_demo_required():
                raise CardinalBridgeError(
                    "Required precomputed MedGemma response could not be loaded. "
                    f"scenario={scenario_id}; error={error}"
                ) from error
            print(
                "[KGEN PRECOMPUTED RESPONSE UNAVAILABLE - FALLING BACK TO LIVE MODEL]",
                {
                    "scenarioId": scenario_id,
                    "profile": precomputed_profile(),
                    "responseSetId": precomputed_artifact_set_id(),
                    "error": str(error),
                },
                flush=True,
            )

    print(
        "[KGEN UNIVERSAL GROUNDED PIPELINE START]",
        {
            "episodeId": episode_id,
            "incidentId": incident_id,
            "scenarioId": scenario_id,
            "authoritativeDiagnosis": (diagnostic_event.get("diagnosis") or {}).get("display"),
            "modelOverride": model_override,
            "artifactDirectory": str(output_dir),
            "evidenceSchemaVersion": evidence_bundle.get("schemaVersion"),
            "clinicalPromptMode": evidence_bundle.get("clinicalPromptMode"),
            "coverageRequired": sum(
                1
                for item in evidence_bundle.get("coverageRequirements") or []
                if isinstance(item, dict) and item.get("requiredInResponse") is True
            ),
        },
        flush=True,
    )

    first_messages = build_grounded_messages(evidence_bundle=evidence_bundle)
    model_response, model_metadata = await call_grounded_cardinal_model(
        messages=first_messages,
        model_override=model_override,
        temperature=0.0,
        request_log_path=output_dir / "grounded_model_messages.attempt-1.json",
        request_label="attempt-1",
    )

    raw_model_response = (
        model_metadata.get("rawModelOutputV602")
        or model_response
    )
    model_response, normalization_changes = normalize_reviewable_response(
        model_response,
        evidence_bundle,
    )

    first_validation = validate_grounded_response(
        response=model_response,
        diagnostic_event=diagnostic_event,
        supplied_evidence=evidence_bundle,
    )
    _atomic_json(
        output_dir / "cardinal_model_response.attempt-1.json",
        {
            "attempt": 1,
            "createdAt": _now_iso(),
            "model": model_metadata,
            "rawModelResponse": raw_model_response,
            "modelResponse": model_response,
            "normalizationChanges": normalization_changes,
            "validation": first_validation,
        },
    )

    final_validation = first_validation
    attempt_count = 1

    if (
        not first_validation.get("accepted")
        and first_validation.get("retryable", True)
        and _flag("SLM_GROUNDED_RETRY_ENABLED", True)
    ):
        print(
            "[KGEN UNIVERSAL GROUNDED SLM RETRY]",
            {"episodeId": episode_id, "errors": first_validation.get("errors")},
            flush=True,
        )

        retry_messages = build_grounded_messages(
            evidence_bundle=evidence_bundle,
            correction_errors=list(first_validation.get("errors") or []),
            correction_evidence=list(first_validation.get("correctionEvidence") or []),
        )
        model_response, model_metadata = await call_grounded_cardinal_model(
            messages=retry_messages,
            model_override=model_override or _selected_model(model_metadata),
            temperature=0.0,
            request_log_path=output_dir / "grounded_model_messages.attempt-2.json",
            request_label="attempt-2",
        )
        raw_model_response = (
            model_metadata.get("rawModelOutputV602")
            or model_response
        )
        model_response, normalization_changes = normalize_reviewable_response(
            model_response,
            evidence_bundle,
        )

        final_validation = validate_grounded_response(
            response=model_response,
            diagnostic_event=diagnostic_event,
            supplied_evidence=evidence_bundle,
        )
        attempt_count = 2
        _atomic_json(
            output_dir / "cardinal_model_response.attempt-2.json",
            {
                "attempt": 2,
                "createdAt": _now_iso(),
                "model": model_metadata,
                "rawModelResponse": raw_model_response,
                "modelResponse": model_response,
                "normalizationChanges": normalization_changes,
                "validation": final_validation,
            },
        )

    medical_adjudication = await run_medical_validator_review(
        response=model_response,
        validation=final_validation,
        evidence=evidence_bundle,
        artifact_path=output_dir / "medical_validator_review.json",
    )
    final_validation = {
        **final_validation,
        "medicalAdjudication": medical_adjudication,
        # Deterministic quality errors decide whether review is required.
        # The advisory LLM never controls this flag.
        "medicalReviewRecommended": bool(
            final_validation.get("qualityErrors")
        ),
    }

    # Keep the latest raw generation for display. Use the normalized form for
    # deterministic validation and scoring.
    display_cardinal = _final_cardinal_response(
        model_response=raw_model_response,
        diagnostic_event=diagnostic_event,
    )
    validated_cardinal = _final_cardinal_response(
        model_response=model_response,
        diagnostic_event=diagnostic_event,
    )
    reliability = _reliability_metadata(
        attempt_count=attempt_count,
        first_validation=first_validation,
        final_validation=final_validation,
    )

    response_record = {
        "schemaVersion": (
            "grounded-cardinal-response-v4"
            if evidence_bundle.get("schemaVersion") == "slm-evidence-envelope-v4"
            else "grounded-cardinal-response-deterministic-v2"
        ),
        "status": "complete",
        "validationStatus": final_validation.get("groundingStatus") or final_validation.get("status"),
        "displayPolicy": "always_show_model_response",
        "createdAt": _now_iso(),
        "mode": "evaluation_injection",
        "scenarioId": scenario_id,
        "episodeId": episode_id,
        "incidentId": incident_id,
        "source": (
            "authoritative_diagnosis_plus_scoped_evidence"
            if evidence_bundle.get("schemaVersion") == "slm-evidence-envelope-v4"
            else "authoritative_diagnosis_plus_deterministic_universal_evidence"
        ),
        "evidenceSchemaVersion": evidence_bundle.get("schemaVersion"),
        "clinicalPromptMode": evidence_bundle.get("clinicalPromptMode"),
        "evidenceConsistencyReview": consistency_review,
        "validatorVersion": final_validation.get("policyVersion"),
        "diagnosticEvent": diagnostic_event,
        "evidenceFingerprint": _fingerprint(evidence_bundle),
        "model": model_metadata,
        "modelOwnedFields": [
            "episodeSummary",
            "detectedEpisodeContext",
            "mostLikelyEtiology",
            "contributingFactors",
            "uncertaintyAndMissingData",
        ],
        "deterministicFields": [
            "rhythmInterpretation",
            "recommendedImmediateActions",
        ],
        "recommendedActionsRequired": False,
        "modelResponse": display_cardinal,
        "displayModelResponse": display_cardinal,
        "validatedModelResponse": validated_cardinal,
        "rawGroundedModelResponse": raw_model_response,
        "normalizedGroundedModelResponse": model_response,
        "normalizationChanges": normalization_changes,
        "validation": final_validation,
        "reliability": reliability,
        **reliability,
    }
    response_path = output_dir / "cardinal_model_response.json"

    # Persist model output before loading hidden scenario answer-key data.
    _atomic_json(response_path, response_record)

    phase7_slm_response: dict[str, Any] = {}
    if update_phase7_storage:
        phase7_slm_response = _attach_to_phase7_storage(
            incident_id=incident_id,
            diagnostic_event=diagnostic_event,
            cardinal=display_cardinal,
            validation=final_validation,
            model_metadata=model_metadata,
            response_file=response_path.name,
        )

    scenario_answer_key = load_scenario_answer_key(scenario_id)
    v4_scoring = bool(
        evidence_bundle.get("schemaVersion") == "slm-evidence-envelope-v4"
        and _flag("SLM_BENCHMARK_ALIGNMENT_ENABLED", False)
    )
    benchmark_alignment = str(
        evidence_bundle.get("benchmarkAlignmentMode")
        or (
            "oracle_only_limited"
            if evidence_bundle.get("clinicalPromptMode") == "oracle_only"
            else "scoped_context_limited"
            if (
                evidence_bundle.get("clinicalPromptMode")
                == "controlled_event_plus_oracle"
            )
            else "full_scenario"
        )
    )
    scoring_kwargs: dict[str, Any] = {}
    if v4_scoring:
        scoring_kwargs = {
            "benchmark_alignment_mode": benchmark_alignment,
            "clinical_prompt_mode": evidence_bundle.get("clinicalPromptMode"),
            "scoped_evidence": evidence_bundle,
        }
    score = score_etiology_context_response(
        episode_id=scenario_id,
        model_response=validated_cardinal,
        diagnostic_event=diagnostic_event,
        validation=final_validation,
        answer_key=scenario_answer_key,
        **scoring_kwargs,
    )
    episode_pack_only = (
        evidence_bundle.get(
            "clinicalPromptMode"
        )
        == "episode_pack_only"
    )

    oracle_bound = bool(
        not episode_pack_only
        and (
            evidence_bundle.get(
                "modelContextMode"
            )
            in {
                "oracle_bound",
                "oracle_only",
                "controlled_event_plus_oracle",
            }
            or (
                evidence_bundle.get(
                    "schemaVersion"
                )
                == "slm-evidence-envelope-v4"
                and evidence_bundle.get(
                    "clinicalPromptMode"
                )
                != "episode_pack_only"
            )
        )
    )

    score.update(
        {
            "normalizedModelResponse": validated_cardinal,
            "displayModelResponse": display_cardinal,
            "cardinalResponseFile": response_path.name,
            "diagnosticEvent": diagnostic_event,
            "responseValidation": final_validation,
            "rhythmIdentificationSource": "authoritative_diagnostic_event",
            "scoreApplicability": (
                "controlled_scenario_benchmark_only"
                if oracle_bound
                else "scenario_evaluation"
            ),
            "clinicalPatientScoreApplicable": not oracle_bound,
            "displayInClinicalWidget": True,
            "displayPolicy": "always_show_model_response",
            "evidenceSchemaVersion": evidence_bundle.get("schemaVersion"),
            "clinicalPromptMode": evidence_bundle.get("clinicalPromptMode"),
            "benchmarkAlignmentMode": benchmark_alignment,
            "evidenceConsistencyReview": consistency_review,
            "generationAttempted": True,
            "validContract": True,
            "reliability": reliability,
            **reliability,
        }
    )

    if not _validation_hard_pass(final_validation):
        score["validatorPassed"] = False
        score["safetyPass"] = False
        score["overallPass"] = False
        score["validationRejected"] = True

    _atomic_json(output_dir / "evaluation_score.json", score)
    if evidence_bundle.get("schemaVersion") == "slm-evidence-envelope-v4":
        _atomic_json(
            output_dir / "grounding_validation_v4.json",
            {
                "schemaVersion": "grounding-validation-v4",
                **final_validation,
            },
        )
        _atomic_json(
            output_dir / "benchmark_result_v4.json",
            {
                "schemaVersion": "benchmark-result-v4",
                **score,
            },
        )
        if _flag("SLM_WIDGET_DTO_V4_ENABLED", False):
            _atomic_json(
                output_dir / "slm_widget_result_v4.json",
                _widget_dto_v4(
                    scenario_id=scenario_id,
                    episode_id=episode_id,
                    incident_id=incident_id,
                    cardinal=display_cardinal,
                    diagnostic_event=diagnostic_event,
                    validation=final_validation,
                    score=score,
                    evidence_bundle=evidence_bundle,
                    model_metadata=model_metadata,
                ),
            )

    print(
        "[KGEN UNIVERSAL GROUNDED PIPELINE COMPLETE]",
        {
            "episodeId": episode_id,
            "scenarioId": scenario_id,
            "validation": final_validation.get("status"),
            "attemptCount": attempt_count,
            "firstAttemptAccepted": reliability["firstAttemptAccepted"],
            "score": score.get("total"),
            "overallPass": score.get("overallPass"),
            "evidenceCoverage": (
                reliability["evidenceCoverageCount"],
                reliability["evidenceCoverageRequired"],
            ),
        },
        flush=True,
    )

    return {
        "status": "complete",
        "validationStatus": final_validation.get("groundingStatus") or final_validation.get("status"),
        "source": (
            "authoritative_diagnosis_plus_scoped_evidence"
            if evidence_bundle.get("schemaVersion") == "slm-evidence-envelope-v4"
            else "authoritative_diagnosis_plus_deterministic_universal_evidence"
        ),
        "evidenceSchemaVersion": evidence_bundle.get("schemaVersion"),
        "clinicalPromptMode": evidence_bundle.get("clinicalPromptMode"),
        "diagnosticEvent": diagnostic_event,
        "modelResponse": display_cardinal,
        "displayModelResponse": display_cardinal,
        "validatedModelResponse": validated_cardinal,
        "model": model_metadata,
        "validation": final_validation,
        "score": score,
        "reliability": reliability,
        "phase7SlmResponse": phase7_slm_response,
        "responseFile": response_path.name,
        "artifactDirectory": str(output_dir),
        "evidenceConsistencyReview": consistency_review,
        "generationAttempted": True,
        "validContract": True,
    }


async def build_score_and_attach_cardinal(
    *,
    scenario_id: str,
    episode_id: str,
    incident_id: str,
    phase7_result: dict[str, Any],
    episode_dir: Path,
    scenario_payload: dict[str, Any],
    capture_evidence: dict[str, Any],
) -> dict[str, Any]:
    diagnostic_event, evidence_bundle = normalize_scenario_evidence(
        scenario_id=scenario_id,
        episode_id=episode_id,
        incident_id=incident_id,
        scenario_payload=scenario_payload,
        capture_evidence=capture_evidence,
    )

    # Preserve the complete scenario package so episode-pack-only scoping can
    # include the patient name, full history, vitals, labs, medications,
    # clinical context, and scenario ECG measurements. Oracle launch metadata
    # is removed by sanitize_complete_episode_pack().
    evidence_bundle["completeEpisodePack"] = (
        sanitize_complete_episode_pack(
            scenario_payload
        )
    )

    # Scope exactly once inside the runtime pipeline. This preserves the full
    # normalized source bundle until the V4 prompt mode is selected.
    return await _run_grounded_pipeline(
        scenario_id=scenario_id,
        episode_id=episode_id,
        incident_id=incident_id,
        source_episode_dir=episode_dir,
        diagnostic_event=diagnostic_event,
        evidence_bundle=evidence_bundle,
        model_override=None,
        artifact_dir=episode_dir,
        update_phase7_storage=True,
        phase7_evidence=_phase7_evidence_for_scope(incident_id, phase7_result),
    )


async def rerun_grounded_from_saved_input(
    *,
    episode_dir: Path,
    model_override: str | None = None,
    artifact_dir: Path | None = None,
    update_phase7_storage: bool = False,
) -> dict[str, Any]:
    input_path = episode_dir / "grounded_model_input.json"

    if not input_path.exists():
        raise FileNotFoundError(f"Grounded input file not found: {input_path}")

    record = json.loads(
        input_path.read_text(
            encoding="utf-8"
        )
    )

    stored_diagnostic_event = (
        record.get("diagnosticEvent")
        or {}
    )
    stored_evidence = (
        record.get("evidenceBundle")
        or record.get("suppliedEvidence")
        or {}
    )

    if (
        stored_evidence.get("schemaVersion")
        == "slm-evidence-envelope-v4"
        and stored_evidence.get(
            "clinicalPromptMode"
        )
        == EPISODE_PACK_MODE
    ):
        diagnostic_event = stored_diagnostic_event
        evidence_bundle = stored_evidence
    else:
        diagnostic_event, evidence_bundle = (
            rebuild_from_saved_input(record)
        )

    scenario_id = str(
        record.get("scenarioId")
        or (diagnostic_event.get("source") or {}).get("identifier")
        or ""
    )
    episode_id = str(record.get("episodeId") or diagnostic_event.get("episodeId") or episode_dir.name)
    incident_id = str(record.get("incidentId") or diagnostic_event.get("incidentId") or "")

    if not scenario_id or not episode_id or not incident_id:
        raise CardinalBridgeError(
            "Saved grounded input is missing scenario, episode, or incident identifiers."
        )

    if (
        evidence_bundle.get("schemaVersion")
        != "slm-evidence-envelope-v4"
        or evidence_bundle.get(
            "clinicalPromptMode"
        )
        != EPISODE_PACK_MODE
    ):
        evidence_bundle["completeEpisodePack"] = (
            _load_complete_episode_pack(
                scenario_id
            )
        )

    return await _run_grounded_pipeline(
        scenario_id=scenario_id,
        episode_id=episode_id,
        incident_id=incident_id,
        source_episode_dir=episode_dir,
        diagnostic_event=diagnostic_event,
        evidence_bundle=evidence_bundle,
        model_override=model_override,
        artifact_dir=artifact_dir or episode_dir,
        update_phase7_storage=update_phase7_storage,
    )
