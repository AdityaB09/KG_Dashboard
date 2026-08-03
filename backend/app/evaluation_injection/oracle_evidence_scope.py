from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from typing import Any


V4_SCHEMA = "slm-evidence-envelope-v4"
V4_VALIDATOR_SCHEMA = "validator-contract-v4"
_ALLOWED_PROMPT_MODES = {"oracle_only", "controlled_event_plus_oracle"}


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _fingerprint(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _prompt_mode() -> str:
    mode = os.getenv("SLM_PROMPT_MODE", "oracle_only").strip().lower()
    return mode if mode in _ALLOWED_PROMPT_MODES else "oracle_only"


def _event_match_terms(evidence: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    diagnosis = evidence.get("authoritativeDiagnosis") or {}
    rhythm = evidence.get("rhythmFeatures") or {}

    for value in (
        diagnosis.get("display"),
        diagnosis.get("code"),
        rhythm.get("regularity"),
        rhythm.get("ventricularRateBpm"),
        rhythm.get("qrsDurationMs"),
    ):
        text = _text(value)
        if text and text not in terms:
            terms.append(text)

    for finding in rhythm.get("findings") or []:
        text = _text(finding)
        if text and text not in terms:
            terms.append(text)

    return terms[:12]


def _phase7_ecg(phase7_evidence: dict[str, Any] | None) -> dict[str, Any]:
    package = phase7_evidence or {}
    evidence = package.get("evidence") or {}
    measured = evidence.get("independentlyMeasuredEcg") or {}
    reference = evidence.get("datasetReference") or {}

    if not measured and not reference:
        return {}

    return {
        "source": "phase6_deterministic_analysis",
        "analysisStatus": package.get("analysisStatus"),
        "signalQuality": deepcopy(measured.get("signalQuality") or {}),
        "rhythm": deepcopy(measured.get("rhythm") or {}),
        "qrs": deepcopy(measured.get("qrs") or {}),
        "morphology": deepcopy(measured.get("morphology") or {}),
        "leadAgreement": deepcopy(measured.get("leadAgreement") or {}),
        "crossEpisodeAgreement": deepcopy(
            measured.get("crossEpisodeAgreement") or {}
        ),
        "confidence": deepcopy(measured.get("confidence") or {}),
        "independentCandidateDetection": deepcopy(
            measured.get("independentCandidateDetection") or {}
        ),
        "datasetReference": deepcopy(reference),
        "limitations": deepcopy(package.get("limitations") or []),
    }




def _relative_minutes(item: dict[str, Any]) -> float | None:
    direct = _finite(item.get("minutesFromAnchor"))
    if direct is not None:
        return direct
    label = _text(item.get("latestRelationLabel") or item.get("relationLabel"))
    if not label:
        return None
    try:
        amount = float(label.split()[0])
    except (ValueError, IndexError):
        return None
    return amount if "after" in label.lower() else -amount


def _temporal_bucket(item: dict[str, Any]) -> str:
    existing = _text(item.get("temporalBucket"))
    if existing:
        return existing

    minutes = _finite(item.get("minutesFromAnchor"))
    if minutes is None:
        label = _text(item.get("latestRelationLabel") or item.get("relationLabel"))
        if label:
            try:
                minutes = -abs(float(label.split()[0]))
            except (ValueError, IndexError):
                return "unknown"
        else:
            return "unknown"

    absolute = abs(minutes)
    if absolute <= 60:
        return "episode_near"
    if absolute <= 24 * 60:
        return "within_one_day"
    if absolute <= 7 * 24 * 60:
        return "within_one_week"
    if absolute <= 90 * 24 * 60:
        return "historical"
    return "historical_remote"


def _normalize_trends(items: list[dict[str, Any]], *, limit: int = 12) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        copy = deepcopy(item)
        minutes = _relative_minutes(copy)
        if minutes is not None:
            copy["minutesFromAnchor"] = minutes
            copy["relativeHours"] = round(abs(minutes) / 60.0, 1)
            copy["relativeDays"] = round(abs(minutes) / 1440.0, 1)
        copy["temporalBucket"] = _temporal_bucket(copy)
        copy["isEpisodeNear"] = copy["temporalBucket"] == "episode_near"
        output.append(copy)
    return output


def _med_name(item: dict[str, Any]) -> str:
    return _text(
        item.get("name")
        or item.get("medication")
        or item.get("display")
        or item.get("medicationDisplay")
    )


def _med_evidence_type(item: dict[str, Any]) -> str:
    value = _text(item.get("evidenceLevel") or item.get("resourceType")).lower()
    if "administr" in value:
        return "administration"
    if "dispens" in value:
        return "dispense"
    if "request" in value or "order" in value:
        return "order"
    return value or "record"


def _normalize_medications(items: list[dict[str, Any]], *, limit: int = 16) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    for raw in items:
        if not isinstance(raw, dict):
            continue
        name = _med_name(raw)
        if not name:
            continue

        evidence_type = _med_evidence_type(raw)
        event_time = _text(raw.get("eventTime") or raw.get("authoredOn") or raw.get("date"))
        status = _text(raw.get("status"))
        key = (name.lower(), evidence_type, status.lower(), event_time[:10])

        item = deepcopy(raw)
        item["name"] = name
        item["evidenceType"] = evidence_type
        minutes = _relative_minutes(item)
        if minutes is not None:
            item["minutesFromAnchor"] = minutes
            item["relativeHours"] = round(abs(minutes) / 60.0, 1)
            item["relativeDays"] = round(abs(minutes) / 1440.0, 1)
        item["temporalBucket"] = _temporal_bucket(item)
        item["administrationConfirmed"] = evidence_type == "administration"
        item["exposureSupported"] = evidence_type in {"administration", "dispense"}
        item["causalUseAllowed"] = bool(
            item["exposureSupported"]
            and item["temporalBucket"] in {"episode_near", "within_one_day"}
        )
        deduped[key] = item

    def rank(item: dict[str, Any]) -> tuple[int, int, str]:
        evidence_rank = {
            "administration": 0,
            "dispense": 1,
            "order": 2,
            "record": 3,
        }.get(_text(item.get("evidenceType")), 4)
        temporal_rank = {
            "episode_near": 0,
            "within_one_day": 1,
            "within_one_week": 2,
            "historical": 3,
            "historical_remote": 4,
            "unknown": 5,
        }.get(_text(item.get("temporalBucket")), 6)
        return evidence_rank, temporal_rank, _text(item.get("eventTime"))

    return sorted(deduped.values(), key=rank)[:limit]


def _oracle_context(oracle: dict[str, Any]) -> dict[str, Any]:
    patient = deepcopy(oracle.get("patient") or {})
    summary = deepcopy(oracle.get("patientSummary") or {})

    # The authenticated frontend can retain identifiers, but the model envelope
    # uses minimum-necessary demographics only.
    minimized_patient = {
        "age": patient.get("age") or summary.get("ageAtContextAnchor"),
        "sex": patient.get("sex") or patient.get("gender") or summary.get("gender"),
        "deceased": summary.get("deceased"),
    }

    return {
        "available": True,
        "source": "oracle_smart_fhir",
        "patient": minimized_patient,
        "contextAnchor": deepcopy(oracle.get("contextAnchor") or {}),
        "vitalTrends": _normalize_trends(list(oracle.get("vitalTrends") or [])),
        "labTrends": _normalize_trends(list(oracle.get("labTrends") or [])),
        "medications": _normalize_medications(
            list(oracle.get("medicationTimeline") or [])
        ),
        "conditions": deepcopy(list(oracle.get("conditions") or [])[:12]),
        "encounters": deepcopy(list(oracle.get("encounters") or [])[:8]),
        "diagnosticReports": deepcopy(
            list(oracle.get("diagnosticReports") or [])[:8]
        ),
        "documentMetadata": deepcopy(list(oracle.get("documents") or [])[:8]),
        "resourceAvailability": deepcopy(oracle.get("dataQuality") or {}),
        "retrievalWarnings": deepcopy(
            (oracle.get("sourceResolution") or {}).get("warnings")
            or oracle.get("limitations")
            or []
        ),
    }


def _patient_linkage(
    evidence_bundle: dict[str, Any],
    oracle: dict[str, Any],
) -> dict[str, Any]:
    patient = oracle.get("patient") or {}
    token_id = _text(oracle.get("patientId") or patient.get("id") or patient.get("fhirId"))
    fhir_id = _text(oracle.get("patientId") or patient.get("fhirId") or patient.get("id"))

    source_resolution = oracle.get("sourceResolution") or {}
    requested_id = _text(
        source_resolution.get("requestedPatientId")
        or source_resolution.get("patientId")
        or token_id
    )
    episode_id = _text(
        evidence_bundle.get("episodePatientId")
        or evidence_bundle.get("patientId")
        or token_id
    )
    oracle_demo_id = _text(
        evidence_bundle.get("oracleDemoPatientId")
        or source_resolution.get("oracleDemoPatientId")
        or token_id
    )

    nonempty = [item for item in (token_id, fhir_id, requested_id, episode_id, oracle_demo_id) if item]
    same = bool(nonempty) and len(set(nonempty)) == 1
    warnings: list[str] = []
    if not nonempty:
        warnings.append("Oracle patient identifiers were unavailable.")
    elif not same:
        warnings.append("Oracle token, FHIR, and episode patient identifiers do not match.")

    return {
        "tokenPatientId": token_id or None,
        "fhirPatientId": fhir_id or None,
        "episodePatientId": episode_id or None,
        "oracleDemoPatientId": oracle_demo_id or None,
        "samePatientVerified": same,
        "linkageMode": _text(oracle.get("linkageMode") or "patient_linked"),
        "warnings": warnings,
    }


def _episode(evidence: dict[str, Any], deterministic: dict[str, Any]) -> dict[str, Any]:
    capture = evidence.get("capture") or {}
    return {
        "episodeId": evidence.get("episodeId"),
        "incidentId": evidence.get("incidentId"),
        "scenarioId": evidence.get("scenarioId"),
        "demoRunId": evidence.get("demoRunId"),
        "mode": "evaluation_injection",
        "waveformSource": "physionet-incart",
        "sampleRateHz": capture.get("sampleRateHz") or capture.get("sampleRate"),
        "leads": deepcopy(capture.get("leads") or []),
        "captureSeconds": capture.get("durationSeconds"),
        "preEventSeconds": capture.get("preSeconds"),
        "eventSeconds": capture.get("eventSeconds"),
        "postEventSeconds": capture.get("postSeconds"),
        "captureComplete": capture.get("complete"),
        "signalValidationPassed": bool(
            (deterministic.get("signalQuality") or {}).get("status") == "ready"
        ),
    }


def _controlled_context(evidence: dict[str, Any], *, included: bool) -> dict[str, Any]:
    if not included:
        return {
            "included": False,
            "source": "controlled_evaluation_scenario",
            "hemodynamics": {},
            "ischemia": {},
            "electrolytes": {},
            "infection": {},
            "renal": {},
            "qt": {},
            "toxicity": {},
            "metabolic": {},
        }

    laboratory = deepcopy(evidence.get("laboratoryContext") or {})
    metabolic: dict[str, Any] = {}
    for key in ("lactate", "ph", "bicarbonate", "glucose"):
        if key in laboratory:
            metabolic[key] = deepcopy(laboratory.get(key))

    return {
        "included": True,
        "source": "controlled_evaluation_scenario",
        "hemodynamics": deepcopy(evidence.get("hemodynamicStatus") or {}),
        "ischemia": deepcopy(evidence.get("ischemiaContext") or {}),
        "electrolytes": deepcopy(evidence.get("electrolyteContext") or {}),
        "infection": deepcopy(evidence.get("infectionContext") or {}),
        "renal": deepcopy(evidence.get("renalContext") or {}),
        "qt": deepcopy(evidence.get("qtContext") or {}),
        "toxicity": deepcopy(evidence.get("toxicityContext") or {}),
        "metabolic": metabolic,
    }


def _measurement_conflicts(
    rhythm: dict[str, Any],
    deterministic: dict[str, Any],
) -> list[dict[str, Any]]:
    if not _flag("SLM_CONFLICT_DETECTION_ENABLED", False):
        return []

    conflicts: list[dict[str, Any]] = []
    measured_rhythm = deterministic.get("rhythm") or {}
    qrs = deterministic.get("qrs") or {}

    controlled_hr = _finite(rhythm.get("ventricularRateBpm"))
    measured_hr = _finite(measured_rhythm.get("medianHeartRateBpm"))
    if controlled_hr is not None and measured_hr is not None:
        delta = abs(controlled_hr - measured_hr)
        threshold = max(10.0, abs(controlled_hr) * 0.15)
        if delta > threshold:
            conflicts.append(
                {
                    "id": "heart_rate_conflict",
                    "material": True,
                    "controlledValue": controlled_hr,
                    "independentValue": measured_hr,
                    "unit": "bpm",
                    "difference": round(delta, 3),
                    "requiredAcknowledgement": (
                        "State that the controlled event rate and independent "
                        "Phase 6 median rate differ without changing the fixed diagnosis."
                    ),
                }
            )

    controlled_qrs = _finite(rhythm.get("qrsDurationMs"))
    measured_qrs = _finite(qrs.get("medianTriggerQrsDurationMilliseconds"))
    if controlled_qrs is not None and measured_qrs is not None:
        delta = abs(controlled_qrs - measured_qrs)
        threshold = max(12.0, abs(controlled_qrs) * 0.20)
        if delta > threshold:
            conflicts.append(
                {
                    "id": "qrs_duration_conflict",
                    "material": True,
                    "controlledValue": controlled_qrs,
                    "independentValue": measured_qrs,
                    "unit": "ms",
                    "difference": round(delta, 3),
                    "requiredAcknowledgement": (
                        "State that the controlled QRS duration and independent "
                        "Phase 6 trigger QRS measurement differ."
                    ),
                }
            )

    return conflicts


def _available(context: dict[str, Any]) -> bool:
    if not context:
        return False
    if context.get("available") is False:
        return False
    return any(
        value not in (None, "", [], {}, False)
        for key, value in context.items()
        if key != "available"
    ) or context.get("available") is True


def _contract_requirement(
    identifier: str,
    instruction: str,
    *,
    required: bool,
    match_terms: list[str],
    match_groups: list[list[str]] | None = None,
    evidence_paths: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "instruction": instruction,
        "requiredInResponse": required,
        "matchTerms": match_terms,
        "matchAllGroups": match_groups or [],
        "evidencePaths": evidence_paths or [],
    }


def _validator_contract(
    envelope: dict[str, Any],
    *,
    evidence_fingerprint: str,
) -> dict[str, Any]:
    controlled = envelope.get("controlledEventContext") or {}
    required: list[dict[str, Any]] = [
        _contract_requirement(
            "rhythmEvidence",
            "Describe the fixed controlled rhythm and supplied rhythm evidence without introducing a competing diagnosis.",
            required=True,
            match_terms=_event_match_terms(
                {
                    "authoritativeDiagnosis": (envelope.get("controlledRhythm") or {}).get("diagnosis") or {},
                    "rhythmFeatures": envelope.get("controlledRhythm") or {},
                }
            ),
            evidence_paths=["controlledRhythm"],
        )
    ]
    optional: list[dict[str, Any]] = []

    if controlled.get("included"):
        hemodynamics = controlled.get("hemodynamics") or {}
        if _available(hemodynamics):
            required.append(
                _contract_requirement(
                    "hemodynamicContext",
                    "Interpret the supplied controlled-event hemodynamic state and label it as controlled event evidence.",
                    required=True,
                    match_terms=[
                        "hemodynamic",
                        "pulseless",
                        "cardiac arrest",
                        "blood pressure",
                        "shock",
                        "stable",
                        "unstable",
                    ],
                    evidence_paths=["controlledEventContext.hemodynamics"],
                )
            )

        ischemia = controlled.get("ischemia") or {}
        if _available(ischemia):
            required.append(
                _contract_requirement(
                    "ischemiaInterpretation",
                    "Interpret the supplied controlled-event chest-pain, ST-segment, and troponin evidence.",
                    required=True,
                    match_terms=[
                        "ischemia",
                        "stemi",
                        "st elevation",
                        "troponin",
                        "chest pain",
                        "coronary",
                    ],
                    evidence_paths=["controlledEventContext.ischemia"],
                )
            )

        electrolytes = controlled.get("electrolytes") or {}
        if _available(electrolytes):
            major = bool(
                electrolytes.get("majorElectrolyteTriggerSupported")
                or electrolytes.get("electrolyteAbnormalityPresent")
                or electrolytes.get("abnormalElectrolytes")
            )
            target = required if major else optional
            target.append(
                _contract_requirement(
                    "electrolyteInterpretation",
                    "Explain whether the supplied controlled-event electrolyte evidence supports or argues against an electrolyte contribution.",
                    required=major,
                    match_terms=[
                        "potassium",
                        "magnesium",
                        "electrolyte",
                        "within reference",
                        "normal",
                        "abnormal",
                        "not supported",
                    ],
                    evidence_paths=["controlledEventContext.electrolytes"],
                )
            )

        infection = controlled.get("infection") or {}
        infection_required = bool(
            infection.get("infectionSupported")
            or infection.get("sepsisSupported")
            or infection.get("inflammatoryResponseSupported")
        )
        if _available(infection):
            (required if infection_required else optional).append(
                _contract_requirement(
                    "infectionContext",
                    "Interpret the supplied controlled-event infection or inflammatory evidence.",
                    required=infection_required,
                    match_terms=[
                        "infection",
                        "sepsis",
                        "fever",
                        "wbc",
                        "procalcitonin",
                        "lactate",
                    ],
                    evidence_paths=["controlledEventContext.infection"],
                )
            )

        renal = controlled.get("renal") or {}
        renal_required = bool(
            renal.get("renalImpairmentSupported")
            or renal.get("acuteKidneyInjurySupported")
            or renal.get("chronicKidneyDiseaseSupported")
            or renal.get("dialysis")
            or renal.get("esrd")
        )
        if _available(renal):
            (required if renal_required else optional).append(
                _contract_requirement(
                    "renalContext",
                    "Interpret supplied controlled-event renal function or dialysis evidence.",
                    required=renal_required,
                    match_terms=["renal", "kidney", "creatinine", "dialysis", "clearance", "esrd"],
                    evidence_paths=["controlledEventContext.renal"],
                )
            )

        qt = controlled.get("qt") or {}
        qt_required = bool(qt.get("prolonged") or qt.get("acquiredLongQtSupported"))
        if _available(qt):
            (required if qt_required else optional).append(
                _contract_requirement(
                    "qtContext",
                    "Interpret the supplied controlled-event QT evidence.",
                    required=qt_required,
                    match_terms=["qt", "qtc", "long qt", "prolonged", "torsades"],
                    evidence_paths=["controlledEventContext.qt"],
                )
            )

        toxicity = controlled.get("toxicity") or {}
        toxicity_required = bool(
            toxicity.get("digoxinToxicitySupported")
            or toxicity.get("reducedClearanceSupported")
        )
        if _available(toxicity):
            (required if toxicity_required else optional).append(
                _contract_requirement(
                    "medicationToxicityContext",
                    "Interpret the supplied controlled-event toxicity evidence.",
                    required=toxicity_required,
                    match_terms=["toxicity", "digoxin", "drug level", "clearance"],
                    evidence_paths=["controlledEventContext.toxicity"],
                )
            )

    for conflict in envelope.get("measurementConflicts") or []:
        if conflict.get("material"):
            required.append(
                _contract_requirement(
                    "measurementConflict",
                    _text(conflict.get("requiredAcknowledgement"))
                    or "Acknowledge the supplied material measurement conflict.",
                    required=True,
                    match_terms=[
                        "differs",
                        "discrepancy",
                        "conflict",
                        "independent",
                        "controlled",
                    ],
                    evidence_paths=["measurementConflicts"],
                )
            )
            break

    optional.extend(
        [
            _contract_requirement(
                "oracleClinicalContext",
                "Use available Oracle FHIR facts only with their temporal qualification.",
                required=False,
                match_terms=["historical", "before event", "Oracle", "FHIR"],
                evidence_paths=["oracleContext"],
            ),
            _contract_requirement(
                "etiologySupport",
                "State a supported etiology or explicitly state that the supplied context does not establish one.",
                required=False,
                match_terms=[
                    "not established",
                    "does not establish",
                    "insufficient",
                    "supports",
                    "most likely",
                ],
                evidence_paths=["controlledEventContext", "oracleContext"],
            ),
        ]
    )

    numeric_evidence: list[dict[str, Any]] = []

    def collect_numbers(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                collect_numbers(item, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                collect_numbers(item, f"{path}[{index}]")
        else:
            number = _finite(value)
            if number is not None:
                numeric_evidence.append({"value": number, "path": path})

    collect_numbers(envelope.get("controlledRhythm") or {}, "controlledRhythm")
    collect_numbers(envelope.get("controlledEventContext") or {}, "controlledEventContext")
    collect_numbers(envelope.get("deterministicAnalysis") or {}, "deterministicAnalysis")
    collect_numbers(envelope.get("oracleContext") or {}, "oracleContext")
    collect_numbers(envelope.get("detector") or {}, "detector")

    return {
        "schemaVersion": V4_VALIDATOR_SCHEMA,
        "clinicalPromptMode": envelope.get("clinicalPromptMode"),
        "evidenceFingerprint": evidence_fingerprint,
        "requiredCoverage": [item["id"] for item in required],
        "optionalCoverage": [item["id"] for item in optional],
        "coverageRequirements": [*required, *optional],
        "forbiddenClaims": [
            "independent_competing_rhythm",
            "invented_patient_fact",
            "false_negative_claim",
            "treatment_recommendation",
            "medication_order_as_administration",
            "unsupported_causal_claim",
        ],
        "numericEvidence": numeric_evidence,
        "temporalRules": [
            "Historical and historical_remote Oracle evidence must not be described as current or episode-time.",
            "Missing evidence must not be converted into a negative finding.",
        ],
        "causalRules": [
            "Oracle medication orders do not establish exposure or causation.",
            "Remote Oracle evidence cannot establish the cause of the controlled event.",
            "Controlled event facts may support etiology only in controlled_event_plus_oracle mode.",
        ],
        "sourceRules": [
            "Oracle SMART FHIR owns patient facts.",
            "The controlled evaluation source owns the fixed event diagnosis and controlled event facts.",
            "Phase 6 owns independent waveform measurements.",
        ],
    }


def _missing_evidence(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    deterministic = envelope.get("deterministicAnalysis") or {}
    oracle = envelope.get("oracleContext") or {}

    if not deterministic:
        missing.append({"id": "phase6_analysis", "source": "phase6", "reason": "unavailable"})
    if not oracle.get("vitalTrends"):
        missing.append({"id": "oracle_vitals", "source": "oracle_smart_fhir", "reason": "unavailable"})
    if not oracle.get("labTrends"):
        missing.append({"id": "oracle_labs", "source": "oracle_smart_fhir", "reason": "unavailable"})
    if not oracle.get("conditions"):
        missing.append(
            {
                "id": "oracle_conditions",
                "source": "oracle_smart_fhir",
                "reason": "not_loaded_or_none_returned",
                "negativeFinding": False,
            }
        )
    return missing


def _legacy_scope(
    evidence_bundle: dict[str, Any],
    *,
    phase7_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    oracle = evidence_bundle.get("oraclePatientContext") or {}
    if not oracle.get("available"):
        return evidence_bundle

    deterministic = _phase7_ecg(phase7_evidence)
    scoped: dict[str, Any] = {
        "schemaVersion": "grounded-evidence-oracle-bound-v1",
        "scenarioId": evidence_bundle.get("scenarioId"),
        "episodeId": evidence_bundle.get("episodeId"),
        "incidentId": evidence_bundle.get("incidentId"),
        "modelContextMode": "oracle_bound",
        "authoritativeDiagnosis": deepcopy(evidence_bundle.get("authoritativeDiagnosis") or {}),
        "sourceSeparation": {
            "eventSource": "controlled_evaluation_scenario",
            "patientContextSource": "oracle_smart_fhir",
            "scenarioPatientFactsIncluded": False,
            "oracleContextDoesNotOwnDiagnosis": True,
            "causalClaimsRequireDirectSupport": True,
        },
        "rhythmFeatures": deepcopy(evidence_bundle.get("rhythmFeatures") or {}),
        "detector": deepcopy(evidence_bundle.get("detector") or {}),
        "capture": deepcopy(evidence_bundle.get("capture") or {}),
        "capturedDeterministicAnalysis": deterministic,
        "oraclePatientContext": deepcopy(oracle),
        "availability": {
            "controlledEvent": True,
            "capturedDeterministicAnalysis": bool(deterministic),
            "oraclePatientContext": True,
        },
        "coverageRequirements": [
            {
                "id": "rhythmEvidence",
                "instruction": (
                    "Describe the fixed event diagnosis and the supplied rhythm "
                    "measurements without introducing another rhythm diagnosis."
                ),
                "matchTerms": _event_match_terms(evidence_bundle),
                "matchAllGroups": [],
                "requiredInResponse": True,
            },
            {
                "id": "oracleClinicalContext",
                "instruction": (
                    "Use only available Oracle FHIR facts for patient context and "
                    "state their timing when relevant."
                ),
                "matchTerms": ["Oracle", "FHIR", "historical", "before event", "patient context"],
                "matchAllGroups": [],
                "requiredInResponse": False,
            },
            {
                "id": "etiologySupport",
                "instruction": (
                    "State whether the supplied Oracle context supports a specific "
                    "etiology; otherwise say the etiology is not established."
                ),
                "matchTerms": ["not established", "insufficient", "does not establish", "supports", "most likely"],
                "matchAllGroups": [],
                "requiredInResponse": False,
            },
        ],
    }
    scoped["contexts"] = {
        "controlledEvent": {
            "authoritativeDiagnosis": scoped["authoritativeDiagnosis"],
            "rhythmFeatures": scoped["rhythmFeatures"],
            "detector": scoped["detector"],
            "capture": scoped["capture"],
        },
        "capturedDeterministicAnalysis": deterministic,
        "oraclePatientContext": scoped["oraclePatientContext"],
    }
    return scoped


def scope_oracle_bound_evidence(
    evidence_bundle: dict[str, Any],
    *,
    phase7_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the exact evidence visible to both the model and validator.

    When V4 is disabled this returns the existing V2 Oracle-bound scope. When V4
    is enabled, it builds a versioned evidence envelope and derives the validator
    contract exclusively from that envelope. No hidden answer-key or omitted
    scenario fact can become a grounding requirement.
    """

    if evidence_bundle.get("schemaVersion") == V4_SCHEMA:
        return evidence_bundle

    if not _flag("SLM_EVIDENCE_V4_ENABLED", False):
        return _legacy_scope(
            evidence_bundle,
            phase7_evidence=phase7_evidence,
        )

    oracle = evidence_bundle.get("oraclePatientContext") or {}
    if not oracle.get("available"):
        # Non-Oracle runs retain the existing evidence contract.
        return evidence_bundle

    prompt_mode = _prompt_mode()
    include_controlled = bool(
        prompt_mode == "controlled_event_plus_oracle"
        and _flag("SLM_CONTROLLED_EVENT_CONTEXT_ENABLED", False)
    )
    deterministic = (
        _phase7_ecg(phase7_evidence)
        if _flag("SLM_PHASE6_CONTEXT_ENABLED", False)
        else {}
    )
    rhythm = deepcopy(evidence_bundle.get("rhythmFeatures") or {})

    envelope: dict[str, Any] = {
        "schemaVersion": V4_SCHEMA,
        "clinicalPromptMode": prompt_mode,
        "task": {
            "taskType": "episode_contextual_interpretation",
            "fixedDiagnosisMustBePreserved": True,
            "independentRhythmDiagnosisAllowed": False,
            "treatmentRecommendationAllowed": False,
            "causalCertaintyRequired": False,
        },
        "patientLinkage": _patient_linkage(evidence_bundle, oracle),
        "sourceManifest": {
            "patientContextSource": "oracle_smart_fhir",
            "controlledEventSource": "controlled_evaluation_scenario",
            "waveformAnalysisSource": "phase6_deterministic_analysis",
            "detectorSource": "multilead_waveform_change_detector",
            "benchmarkSource": "hidden_scenario_answer_key",
        },
        "episode": _episode(evidence_bundle, deterministic),
        "controlledRhythm": {
            "source": "controlled_evaluation_scenario",
            "diagnosis": deepcopy(evidence_bundle.get("authoritativeDiagnosis") or {}),
            **rhythm,
        },
        "controlledEventContext": _controlled_context(
            evidence_bundle,
            included=include_controlled,
        ),
        "detector": deepcopy(evidence_bundle.get("detector") or {}),
        "deterministicAnalysis": deterministic,
        "measurementConflicts": _measurement_conflicts(rhythm, deterministic),
        "oracleContext": _oracle_context(oracle),
        "missingEvidence": [],
        "outputContract": {
            "allowedFields": [
                "episodeSummary",
                "detectedEpisodeContext",
                "mostLikelyEtiology",
                "contributingFactors",
                "uncertaintyAndMissingData",
            ],
            "additionalFieldsAllowed": False,
        },
    }
    envelope["missingEvidence"] = _missing_evidence(envelope)

    base_for_fingerprint = deepcopy(envelope)
    evidence_fingerprint = _fingerprint(base_for_fingerprint)
    envelope["evidenceFingerprint"] = evidence_fingerprint
    contract = _validator_contract(
        envelope,
        evidence_fingerprint=evidence_fingerprint,
    )
    envelope["validatorContract"] = contract
    envelope["coverageRequirements"] = deepcopy(contract["coverageRequirements"])
    envelope["modelContextMode"] = prompt_mode

    # The prompt includes controlled rhythm, hemodynamics, ischemia,
    # electrolytes, infection, renal, QT, toxicity, and metabolic evidence.
    # It does not include the entire hidden scenario history and therefore a
    # full answer-key pass/fail comparison is not fair.
    envelope["benchmarkContextComplete"] = False
    envelope["benchmarkAlignmentMode"] = (
        "scoped_context_limited"
        if (
            prompt_mode == "controlled_event_plus_oracle"
            and include_controlled
        )
        else "oracle_only_limited"
    )
    return envelope
