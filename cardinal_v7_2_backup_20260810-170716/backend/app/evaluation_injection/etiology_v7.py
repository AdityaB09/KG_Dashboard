from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from app.config import settings
from app.evaluation.config import model_evaluation_allowed, slm_model
from app.evaluation.slm_client import EvaluationModelError, call_model


class EtiologyV7Error(RuntimeError):
    """Raised when the V7 etiology-only runtime cannot prepare or load a response."""


V7_RESPONSE_FIELDS: tuple[str, ...] = (
    "episodeSummary",
    "rhythm",
    "keyECGEvidence",
    "primaryEtiology",
    "mechanism",
    "contributingFactors",
    "rejectedAlternatives",
    "recommendedActions",
    "uncertainty",
)

# IMPORTANT: Keep this text synchronized with the V7 evaluation prompt used for
# the uploaded Lightning runs. The episode JSON is appended after EPISODE DATA.
ETIOLOGY_V7_PROMPT = """You are the Etiology Engine of the CARDINAL AI platform — the clinical-reasoning component that determines WHY a monitored patient is deteriorating. A monitoring episode has been captured. Using ONLY the structured data provided —
deterministic ECG measurements, vitals, SpO₂/PPG summary, blood pressure, temperature,
laboratory results, patient record, and clinical context — analyze the episode.

Rules:
- Do not interpret raw waveform samples; reason from the numeric and text fields in
  `ecg.measurements` (rate, regularity, QRS duration, QTc, PR, P-wave presence, ST
  deviation, morphology, ectopy, and the pre-event note).
- Read every free-text field carefully, especially `ecg.measurements.preEventNote`,
  `ecg.measurements.stDeviationMm`, `clinicalContext.recentEvents`, and each lab's
  `flag` — decisive evidence is often there rather than in numeric fields.
- Name the rhythm yourself from the measurements; do not expect it to be given.
- Commit to the SINGLE most likely root cause of the episode (the etiology behind the
  rhythm, not the rhythm itself). List other plausible causes under
  rejectedAlternatives with the specific evidence against each.
- Cite concrete values verbatim (e.g. "K 2.9 mmol/L LOW", "digoxin 3.8 ng/mL TOXIC",
  "troponin T 1.85 CRITICAL HIGH") when giving evidence.
- Recommended actions must be guideline-appropriate, prioritized (most urgent first),
  and safe for THIS patient's full picture — check every action against the patient's
  medications, labs, allergies, and code status before including it. Include what to
  STOP or WITHHOLD as well as what to give.
- If the data are insufficient to support a conclusion, say so explicitly in
  `uncertainty` rather than guessing. Do not invent values that are not in the data.

Respond with ONLY a single JSON object, no other text, in exactly this shape:

{
  "episodeSummary": "<one line: patient, rhythm you identified, severity, hemodynamic status>",
  "rhythm": "<your rhythm/arrhythmia diagnosis>",
  "keyECGEvidence": ["<measurement supporting the rhythm call>", "..."],
  "primaryEtiology": "<the single most probable root cause>",
  "mechanism": "<one or two sentences: how that cause produced this rhythm>",
  "contributingFactors": ["<secondary driver with its supporting value>", "..."],
  "rejectedAlternatives": [
    {"alternative": "<plausible competing cause>", "why": "<specific evidence against it>"}
  ],
  "recommendedActions": ["<action 1, most urgent>", "<action 2>", "..."],
  "uncertainty": ["<what is missing or would change confidence>", "..."]
}

EPISODE DATA"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _dataset_root() -> Path:
    configured = Path(settings.EVALUATION_INJECTION_DATASET_ROOT)
    if not configured.is_absolute():
        configured = _backend_root() / configured
    return configured.resolve()


def _precomputed_root() -> Path:
    configured = Path(
        os.getenv(
            "ETIOLOGY_V7_PRECOMPUTED_ROOT",
            "data/etiology_v7_precomputed",
        ).strip()
    )
    if not configured.is_absolute():
        configured = _backend_root() / configured
    return configured.resolve()


def _precomputed_profile() -> str:
    return (
        os.getenv(
            "ETIOLOGY_V7_PRECOMPUTED_PROFILE",
            "google-medgemma-27b-it",
        ).strip()
        or "google-medgemma-27b-it"
    )


def _precomputed_enabled() -> bool:
    # New V7 flag takes priority. If it is omitted, preserve the deployed demo's
    # existing PRECOMPUTED_SLM_DEMO_ENABLED behavior.
    if os.getenv("ETIOLOGY_V7_PRECOMPUTED_ENABLED") is not None:
        return _flag("ETIOLOGY_V7_PRECOMPUTED_ENABLED", True)
    return _flag("PRECOMPUTED_SLM_DEMO_ENABLED", False)


def _precomputed_required() -> bool:
    if os.getenv("ETIOLOGY_V7_PRECOMPUTED_REQUIRED") is not None:
        return _flag("ETIOLOGY_V7_PRECOMPUTED_REQUIRED", True)
    return _flag("PRECOMPUTED_SLM_DEMO_REQUIRED", True)


def _live_model_enabled() -> bool:
    if os.getenv("ETIOLOGY_V7_LIVE_MODEL_ENABLED") is not None:
        return _flag("ETIOLOGY_V7_LIVE_MODEL_ENABLED", False)
    return model_evaluation_allowed()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def _scenario_path(scenario_id: str) -> Path:
    return _dataset_root() / "episodes" / f"{scenario_id}.json"


def load_scenario_record(scenario_id: str) -> dict[str, Any]:
    path = _scenario_path(scenario_id)
    if not path.exists():
        raise EtiologyV7Error(f"V7 scenario was not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EtiologyV7Error(f"V7 scenario is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise EtiologyV7Error(f"V7 scenario must be a JSON object: {path}")
    if str(payload.get("episodeId") or "").strip() != scenario_id:
        raise EtiologyV7Error(
            "V7 scenario episodeId does not match the requested scenario. "
            f"requested={scenario_id!r}; file={payload.get('episodeId')!r}"
        )
    return payload


def sanitize_episode_for_v7(record: dict[str, Any]) -> dict[str, Any]:
    """Build the exact structured evidence class used by the V7 prompt.

    The raw ECG/PPG arrays are display/injection data only. The model must infer
    the rhythm from measurements, so upstream answer-label fields are removed.
    The original scenario file stays untouched for waveform injection and UI.
    """
    prepared = copy.deepcopy(record)

    ecg = prepared.get("ecg")
    if isinstance(ecg, dict):
        ecg.pop("waveform", None)
        measurements = ecg.get("measurements")
        if isinstance(measurements, dict):
            measurements.pop("rhythm", None)

    ppg = prepared.get("ppg")
    if isinstance(ppg, dict):
        ppg.pop("waveform", None)

    episode = prepared.get("episode")
    if isinstance(episode, dict):
        # These are useful to the display layer, but the V7 model is instructed
        # to name the rhythm itself from measurements.
        episode.pop("type", None)
        episode.pop("display", None)

    # Defense in depth: none of these grading/ground-truth containers may be
    # passed to inference if they ever appear in a future scenario package.
    for key in (
        "answerKey",
        "answer_key",
        "groundTruth",
        "ground_truth",
        "rubric",
        "expectedAnswer",
        "expected_answer",
    ):
        prepared.pop(key, None)

    return prepared


def build_v7_prompt(prepared_episode: dict[str, Any]) -> str:
    episode_json = json.dumps(
        prepared_episode,
        indent=2,
        ensure_ascii=False,
        sort_keys=False,
    )
    return f"{ETIOLOGY_V7_PROMPT}\n{episode_json}"


def _prompt_fingerprint(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def validate_v7_response(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise EtiologyV7Error("V7 model response must be a JSON object.")

    actual = set(payload)
    expected = set(V7_RESPONSE_FIELDS)
    if actual != expected:
        raise EtiologyV7Error(
            "V7 model response must contain exactly the nine required fields. "
            f"missing={sorted(expected - actual)}; extra={sorted(actual - expected)}"
        )

    for key in ("episodeSummary", "rhythm", "primaryEtiology", "mechanism"):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise EtiologyV7Error(f"V7 field {key!r} must be a non-empty string.")

    for key in (
        "keyECGEvidence",
        "contributingFactors",
        "recommendedActions",
        "uncertainty",
    ):
        value = payload.get(key)
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip()
            for item in value
        ):
            raise EtiologyV7Error(f"V7 field {key!r} must be an array of strings.")

    rejected = payload.get("rejectedAlternatives")
    if not isinstance(rejected, list):
        raise EtiologyV7Error("V7 field 'rejectedAlternatives' must be an array.")
    for index, item in enumerate(rejected):
        if not isinstance(item, dict) or set(item) != {"alternative", "why"}:
            raise EtiologyV7Error(
                "Each rejectedAlternatives item must contain exactly "
                f"'alternative' and 'why'. index={index}"
            )
        if any(
            not isinstance(item.get(key), str) or not item[key].strip()
            for key in ("alternative", "why")
        ):
            raise EtiologyV7Error(
                f"rejectedAlternatives[{index}] values must be non-empty strings."
            )

    return payload


def _load_precomputed(scenario_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    profile = _precomputed_profile()
    path = _precomputed_root() / profile / f"{scenario_id}.json"
    if not path.exists():
        raise EtiologyV7Error(
            "V7 precomputed response is missing. "
            f"profile={profile}; scenario={scenario_id}; path={path}"
        )
    try:
        source = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EtiologyV7Error(f"Could not read V7 precomputed response: {path}") from exc

    if not isinstance(source, dict):
        raise EtiologyV7Error(f"V7 precomputed response must be an object: {path}")
    if str(source.get("scenarioId") or "").strip() != scenario_id:
        raise EtiologyV7Error(
            "V7 precomputed scenario mismatch. "
            f"expected={scenario_id}; actual={source.get('scenarioId')}"
        )
    if source.get("generationSucceeded") is False:
        raise EtiologyV7Error(
            f"V7 precomputed generation failed for {profile}/{scenario_id}."
        )
    if source.get("validContract") is False:
        raise EtiologyV7Error(
            f"V7 precomputed output is not contract-valid for {profile}/{scenario_id}."
        )

    response = validate_v7_response(source.get("modelResponse"))
    metadata = {
        "source": "precomputed_v7",
        "precomputed": True,
        "profile": profile,
        "model": source.get("model") or profile,
        "provider": source.get("provider"),
        "runNumber": source.get("runNumber"),
        "elapsedSeconds": source.get("elapsedSeconds"),
        "inputTokens": source.get("inputTokens"),
        "outputTokens": source.get("outputTokens"),
        "peakGpuMemoryGiB": source.get("peakGpuMemoryGiB"),
        "gpuName": source.get("gpuName"),
        "quantization": source.get("quantization"),
        "sourceFile": str(path),
        "sourcePromptFingerprint": source.get("promptFingerprint"),
        "terminalStatus": source.get("terminalStatus"),
        "attemptCount": len(source.get("attempts") or []) or 1,
    }
    return response, metadata


async def _call_live(prompt: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not _live_model_enabled():
        raise EtiologyV7Error(
            "V7 live inference is disabled. Set ETIOLOGY_V7_LIVE_MODEL_ENABLED=true "
            "(or SLM_EVAL_ALLOW_MODEL=true), or enable the precomputed V7 profile."
        )

    started = perf_counter()
    try:
        parsed, model_metadata = await call_model(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
    except EvaluationModelError as exc:
        raise EtiologyV7Error(str(exc)) from exc

    response = validate_v7_response(parsed)
    metadata = {
        "source": "live_v7",
        "precomputed": False,
        "profile": None,
        "model": model_metadata.get("name") or slm_model(),
        "provider": "openai_compatible",
        "runNumber": None,
        "elapsedSeconds": round(perf_counter() - started, 2),
        "inputTokens": (model_metadata.get("usage") or {}).get("prompt_tokens"),
        "outputTokens": (model_metadata.get("usage") or {}).get("completion_tokens"),
        "peakGpuMemoryGiB": None,
        "gpuName": None,
        "quantization": None,
        "sourceFile": None,
        "sourcePromptFingerprint": None,
        "terminalStatus": model_metadata.get("finishReason"),
        "attemptCount": 1,
    }
    return response, metadata


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _metric(key: str, label: str, value: Any, unit: str = "") -> dict[str, Any] | None:
    if value is None or value == "":
        return None
    return {"key": key, "label": label, "value": value, "unit": unit}


def _lab_metric(labs: dict[str, Any], key: str, label: str, candidates: tuple[str, ...]) -> dict[str, Any] | None:
    for candidate in candidates:
        if candidate not in labs:
            continue
        item = labs.get(candidate)
        if isinstance(item, dict):
            return _metric(
                key,
                label,
                item.get("value", item.get("result")),
                str(item.get("unit") or ""),
            )
        return _metric(key, label, item)
    return None


def _widget_metrics(record: dict[str, Any], response: dict[str, Any]) -> list[dict[str, Any]]:
    measurements = ((record.get("ecg") or {}).get("measurements") or {})
    vitals = record.get("vitals") or {}
    bp = vitals.get("bloodPressure") or {}
    labs = record.get("labs") or {}

    hr = (
        measurements.get("ventricularRateBpm")
        if measurements.get("ventricularRateBpm") is not None
        else measurements.get("heartRateBpm")
    )
    if hr is None:
        hr = vitals.get("heartRateBpm")

    bp_display = None
    systolic = bp.get("systolic", vitals.get("systolic"))
    diastolic = bp.get("diastolic", vitals.get("diastolic"))
    if systolic is not None and diastolic is not None:
        bp_display = f"{systolic}/{diastolic}"

    candidates = [
        _metric("model-rhythm", "Model rhythm", response.get("rhythm")),
        _metric("heart-rate", "Heart rate", hr, "bpm"),
        _metric("qrs", "QRS duration", measurements.get("qrsDurationMs"), "ms"),
        _metric("qtc", "QTc", measurements.get("qtcMs"), "ms"),
        _metric("blood-pressure", "Blood pressure", bp_display, "mmHg"),
        _metric("spo2", "SpO₂", vitals.get("spo2Pct"), "%"),
        _lab_metric(labs, "potassium", "Potassium", ("potassium", "Potassium", "K")),
        _lab_metric(labs, "magnesium", "Magnesium", ("magnesium", "Magnesium", "Mg")),
        _lab_metric(labs, "troponin", "Troponin", ("troponinT", "troponinI", "troponin", "Troponin")),
        _lab_metric(labs, "creatinine", "Creatinine", ("creatinine", "Creatinine")),
        _lab_metric(labs, "wbc", "WBC", ("wbc", "WBC", "whiteBloodCellCount")),
        _lab_metric(labs, "lactate", "Lactate", ("lactate", "Lactate")),
    ]
    return [item for item in candidates if item is not None]


def _compatibility_response(response: dict[str, Any]) -> dict[str, Any]:
    etiology = str(response.get("primaryEtiology") or "").strip()
    mechanism = str(response.get("mechanism") or "").strip()
    combined = etiology
    if mechanism:
        combined = f"{etiology}. {mechanism}" if etiology else mechanism

    return {
        "episodeSummary": response.get("episodeSummary") or "",
        "mostLikelyEtiologyAndClinicalContext": combined,
        "contributingFactors": list(response.get("contributingFactors") or []),
        "materialEtiologicUncertainty": list(response.get("uncertainty") or []),
    }


def _widget_interpretation(
    *,
    record: dict[str, Any],
    response: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    episode = record.get("episode") or {}
    etiology = str(response.get("primaryEtiology") or "").strip()
    mechanism = str(response.get("mechanism") or "").strip()
    etiology_context = (
        f"{etiology}. {mechanism}" if etiology and mechanism else etiology or mechanism
    )

    return {
        "severity": episode.get("severity") or "warning",
        "statusLabel": "SLM response generated",
        "displayPolicy": "always_show_model_response",
        "headline": episode.get("display") or response.get("rhythm") or "Evaluation episode",
        "episodeNarrative": response.get("episodeSummary") or "",
        "etiologyContextNarrative": etiology_context,
        "rootCauseNarrative": etiology_context,
        "arrhythmiaNarrative": response.get("rhythm") or "",
        "morphologyNarrative": " ".join(response.get("keyECGEvidence") or []),
        "currentSituation": {"narrative": ""},
        "keyMetrics": _widget_metrics(record, response),
        "possibleContributors": [
            {
                "title": item,
                "confidenceLabel": "model-generated",
                "temporalFit": "episode evidence",
                "evidenceAgainst": [],
            }
            for item in response.get("contributingFactors") or []
        ],
        "importantLimitations": list(response.get("uncertainty") or []),
        "materialEtiologicUncertainty": list(response.get("uncertainty") or []),
        # Kept in the backend DTO for audit/future UI use. The current frontend
        # does not need to change to keep displaying its existing sections.
        "rhythm": response.get("rhythm"),
        "keyECGEvidence": list(response.get("keyECGEvidence") or []),
        "rejectedAlternatives": list(response.get("rejectedAlternatives") or []),
        "recommendedActions": list(response.get("recommendedActions") or []),
        "validationSummary": {
            "status": "contract_valid",
            "strictlyAccepted": True,
            "displayableWithReview": True,
            "validatorPassed": True,
            "hardErrorCount": 0,
            "qualityErrorCount": 0,
            "contradictionCount": 0,
            "unsupportedFactCount": 0,
            "errors": [],
            "hardErrors": [],
            "qualityErrors": [],
            "contradictions": [],
            "unsupportedFacts": [],
        },
        "evaluationStatistics": {
            "scenarioScore": None,
            "overallPass": None,
            "safetyPass": None,
            "attemptCount": metadata.get("attemptCount"),
            "generationLatencySeconds": metadata.get("elapsedSeconds"),
            "rawResponseDisplayed": True,
            "validatedResponseAvailable": True,
        },
    }


def _phase7_compatibility_dir(incident_id: str) -> Path:
    # Existing frontend endpoint is /api/slm-widget/incidents/{incident_id}, and
    # its assembler reads this directory. We retain that storage location only
    # as a compatibility bridge; no Phase 6/Phase 7 orchestrator is executed.
    return Path(settings.INCIDENT_STORAGE_PATH) / "phase7" / incident_id


async def run_etiology_v7(
    *,
    scenario_id: str,
    episode_id: str,
    incident_id: str,
    episode_dir: Path,
    run_slm: bool,
) -> dict[str, Any]:
    record = load_scenario_record(scenario_id)
    prepared = sanitize_episode_for_v7(record)
    prompt = build_v7_prompt(prepared)
    fingerprint = _prompt_fingerprint(prompt)

    prompt_artifact = {
        "schemaVersion": "cardinal-etiology-v7-prompt-v1",
        "scenarioId": scenario_id,
        "episodeId": episode_id,
        "incidentId": incident_id,
        "promptFingerprint": fingerprint,
        "phase6Used": False,
        "phase7OrchestratorUsed": False,
        "oracleFhirContextUsed": False,
        "rawWaveformsIncluded": False,
        "modelInput": prepared,
        "messages": [{"role": "user", "content": prompt}],
    }
    _atomic_json(episode_dir / "etiology_v7_prompt.json", prompt_artifact)

    if not run_slm:
        return {
            "status": "skipped",
            "source": "disabled_for_session",
            "model": None,
            "modelResponse": None,
            "displayModelResponse": None,
            "widgetInterpretation": None,
            "score": {
                "schemaVersion": "etiology-v7-runtime-status-v1",
                "total": None,
                "safetyPass": None,
                "overallPass": None,
                "validContract": None,
            },
            "responseFile": None,
            "validation": {"status": "not_run", "accepted": False},
            "diagnosticEvent": {},
            "storedResponse": {},
        }

    response: dict[str, Any]
    metadata: dict[str, Any]

    if _precomputed_enabled():
        try:
            response, metadata = _load_precomputed(scenario_id)
        except EtiologyV7Error:
            if _precomputed_required():
                raise
            response, metadata = await _call_live(prompt)
    else:
        response, metadata = await _call_live(prompt)

    response = validate_v7_response(response)
    compatibility = _compatibility_response(response)
    widget = _widget_interpretation(record=record, response=response, metadata=metadata)

    source_result = {
        "schemaVersion": "cardinal-etiology-runtime-v7.0.0",
        "createdAt": _now_iso(),
        "scenarioId": scenario_id,
        "episodeId": episode_id,
        "incidentId": incident_id,
        "promptFingerprint": fingerprint,
        "model": metadata.get("model"),
        "source": metadata.get("source"),
        "precomputed": bool(metadata.get("precomputed")),
        "profile": metadata.get("profile"),
        "phase6Used": False,
        "phase7OrchestratorUsed": False,
        "oracleFhirContextUsed": False,
        "validContract": True,
        "modelResponse": response,
        "displayModelResponse": compatibility,
        "modelMetadata": metadata,
    }
    response_path = episode_dir / "etiology_v7_model_response.json"
    _atomic_json(response_path, source_result)

    # Preserve filenames consumed by existing tooling, but make their schema
    # explicitly V7 so they cannot be mistaken for the old four-field pipeline.
    cardinal_compat = {
        "schemaVersion": "cardinal-model-response-v7-compat-v1",
        "scenarioId": scenario_id,
        "episodeId": episode_id,
        "incidentId": incident_id,
        "model": metadata.get("model"),
        "modelResponse": response,
        "displayModelResponse": compatibility,
        "widgetInterpretation": widget,
        "validationStatus": "contract_valid",
        "validationMode": "etiology_v7_contract",
        "phase6Used": False,
        "phase7OrchestratorUsed": False,
        "precomputedResponse": (
            {
                "profile": metadata.get("profile"),
                "scenarioId": scenario_id,
                "sourceArtifactSet": "KGEN V7.0.0 Lightning results",
                "lookupMode": "scenario_id",
            }
            if metadata.get("precomputed")
            else None
        ),
    }
    _atomic_json(episode_dir / "cardinal_model_response.json", cardinal_compat)
    _atomic_json(episode_dir / "slm_widget_result_v4.json", cardinal_compat)

    stored_response = {
        "schemaVersion": "slm-response-etiology-v7-compat-v1",
        "createdAt": _now_iso(),
        "incidentId": incident_id,
        "episodeId": episode_id,
        "scenarioId": scenario_id,
        "modelAlias": metadata.get("model"),
        "model": metadata.get("model"),
        "validationStatus": "contract_valid",
        "validationMode": "etiology_v7_contract",
        "notForClinicalUse": True,
        "warnings": [],
        "response": {
            "schemaVersion": "etiology-v7-widget-payload-v1",
            "scenarioId": scenario_id,
            "modelResponse": response,
            "displayModelResponse": compatibility,
            "widgetInterpretation": widget,
            "responseProvenanceLabel": (
                f"Pre-evaluated {metadata.get('model')} response"
                if metadata.get("precomputed")
                else "Configured model response"
            ),
            "precomputedResponse": (
                {
                    "profile": metadata.get("profile"),
                    "scenarioId": scenario_id,
                    "sourceArtifactSet": "KGEN V7.0.0 Lightning results",
                    "lookupMode": "scenario_id",
                }
                if metadata.get("precomputed")
                else None
            ),
            "modelState": {
                "available": True,
                "modelAlias": metadata.get("model"),
                "precomputed": bool(metadata.get("precomputed")),
                "liveInference": not bool(metadata.get("precomputed")),
            },
        },
    }

    compatibility_dir = _phase7_compatibility_dir(incident_id)
    _atomic_json(compatibility_dir / "slm_response.json", stored_response)
    _atomic_json(
        compatibility_dir / "status.json",
        {
            "schemaVersion": "etiology-v7-compat-status-v1",
            "incidentId": incident_id,
            "state": "completed",
            "stage": "etiology_v7",
            "detail": "V7 etiology response is ready. No Phase 6/Phase 7 analysis was used.",
            "updatedAt": _now_iso(),
        },
    )

    score = {
        "schemaVersion": "etiology-v7-runtime-status-v1",
        "status": "ready",
        "total": None,
        "safetyPass": None,
        "overallPass": None,
        "benchmarkPass": None,
        "validContract": True,
        "generationAttempted": True,
        "validationMode": "json_contract_only",
    }
    _atomic_json(episode_dir / "evaluation_score.json", score)

    return {
        "status": "ready",
        "source": metadata.get("source"),
        "model": metadata.get("model"),
        "modelResponse": response,
        "displayModelResponse": compatibility,
        "widgetInterpretation": widget,
        "score": score,
        "responseFile": str(response_path),
        "validation": {
            "status": "contract_valid",
            "accepted": True,
            "displayableWithReview": True,
            "hardErrors": [],
            "qualityErrors": [],
            "contradictions": [],
            "unsupportedFacts": [],
        },
        "diagnosticEvent": {
            "schemaVersion": "etiology-v7-model-inference-v1",
            "scenarioId": scenario_id,
            "episodeId": episode_id,
            "incidentId": incident_id,
            "rhythm": response.get("rhythm"),
            "primaryEtiology": response.get("primaryEtiology"),
            "phase6Used": False,
        },
        "storedResponse": stored_response,
        "modelMetadata": metadata,
    }
