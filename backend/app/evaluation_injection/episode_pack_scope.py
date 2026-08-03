from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


V4_SCHEMA = "slm-evidence-envelope-v4"
VALIDATOR_SCHEMA = "validator-contract-v4"
PROMPT_MODE = "episode_pack_only"

_ALLOWED_EPISODE_PACK_KEYS = {
    "schemaVersion",
    "episodeId",
    "patient",
    "episode",
    "vitals",
    "labs",
    "medications",
    "clinicalContext",
    "ecg",
    "infusions",
}

_FORBIDDEN_CONTEXT_KEYS = {
    "oracleContext",
    "oraclePatientContext",
    "pairedOraclePatientContext",
    "fhirContext",
    "fhirBaseUrl",
    "accessToken",
    "refreshToken",
    "authorization",
    "token",
    "tokenOverride",
}

_RAW_WAVEFORM_KEYS = {
    "waveform",
    "waveforms",
    "waveformsMv",
    "normalizedWaveforms",
    "leadsMv",
    "samples",
    "rawSamples",
}


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = (
                str(key)
                .strip()
                .lower()
                .replace("_", "")
                .replace("-", "")
            )
            forbidden_keys = {
                str(item)
                .strip()
                .lower()
                .replace("_", "")
                .replace("-", "")
                for item in _FORBIDDEN_CONTEXT_KEYS
            }
            raw_waveform_keys = {
                str(item)
                .strip()
                .lower()
                .replace("_", "")
                .replace("-", "")
                for item in _RAW_WAVEFORM_KEYS
            }
            if (
                normalized_key in forbidden_keys
                or normalized_key in raw_waveform_keys
                or normalized_key == "disclaimer"
                or "oracle" in normalized_key
                or "fhir" in normalized_key
                or "accesstoken" in normalized_key
                or "refreshtoken" in normalized_key
            ):
                continue
            output[str(key)] = _sanitize_value(item)
        return output

    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]

    return value


def sanitize_complete_episode_pack(
    value: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Keep only scenario-package clinical fields and remove launch/FHIR metadata
    and raw waveform arrays before anything reaches the SLM.
    """
    source = value or {}
    output = {
        key: _sanitize_value(source.get(key))
        for key in _ALLOWED_EPISODE_PACK_KEYS
        if key in source
    }

    output.setdefault(
        "schemaVersion",
        "episode-slm-eval-v1",
    )
    return output


def _complete_episode_pack(
    evidence: dict[str, Any],
) -> dict[str, Any]:
    raw = evidence.get("completeEpisodePack")
    if isinstance(raw, dict) and raw:
        return sanitize_complete_episode_pack(raw)

    return sanitize_complete_episode_pack(
        {
            "schemaVersion": "episode-slm-eval-v1",
            "episodeId": evidence.get("scenarioId"),
            "patient": evidence.get("patientContext") or {},
            "episode": {
                "display": (
                    (evidence.get("authoritativeDiagnosis") or {}).get("display")
                ),
                "type": (
                    (evidence.get("authoritativeDiagnosis") or {}).get("code")
                ),
            },
            "vitals": evidence.get("episodeTimeVitals") or {},
            "labs": evidence.get("laboratoryContext") or {},
            "medications": (
                (evidence.get("medicationContext") or {}).get("activeMedications")
                or []
            ),
            "clinicalContext": evidence.get("recentClinicalContext") or {},
            "ecg": {
                "measurements": evidence.get("rhythmFeatures") or {},
            },
        }
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if number != number or number in {
        float("inf"),
        float("-inf"),
    }:
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


def _phase6(
    phase7_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    package = phase7_evidence or {}

    # Accept either the complete evidence_package.json wrapper or its
    # inner `evidence` object.
    evidence = (
        package.get("evidence")
        if isinstance(package.get("evidence"), dict)
        else package
    ) or {}

    measured = (
        evidence.get("independentlyMeasuredEcg")
        or evidence.get("measured")
        or {}
    )
    reference = (
        evidence.get("datasetReference")
        or measured.get("datasetReference")
        or {}
    )

    if not measured and not reference:
        return {}

    return {
        "source": "phase6_deterministic_analysis",
        "analysisStatus": (
            package.get("analysisStatus")
            or evidence.get("analysisStatus")
        ),
        "signalQuality": deepcopy(
            measured.get("signalQuality") or {}
        ),
        "rhythm": deepcopy(
            measured.get("rhythm") or {}
        ),
        "qrs": deepcopy(
            measured.get("qrs") or {}
        ),
        "morphology": deepcopy(
            measured.get("morphology") or {}
        ),
        "leadAgreement": deepcopy(
            measured.get("leadAgreement") or {}
        ),
        "crossEpisodeAgreement": deepcopy(
            measured.get("crossEpisodeAgreement")
            or {}
        ),
        "confidence": deepcopy(
            measured.get("confidence") or {}
        ),
        "independentCandidateDetection": deepcopy(
            measured.get(
                "independentCandidateDetection"
            )
            or {}
        ),
        "datasetReference": deepcopy(reference),
        "limitations": [
            deepcopy(item)
            for item in (
                package.get("limitations")
                or evidence.get("limitations")
                or []
            )
            if "oracle" not in str(item).lower()
            and "fhir" not in str(item).lower()
            and "same-patient" not in str(item).lower()
            and "same patient" not in str(item).lower()
        ],
    }


def _controlled_rhythm(
    evidence: dict[str, Any],
) -> dict[str, Any]:
    diagnosis = deepcopy(
        evidence.get("authoritativeDiagnosis")
        or {}
    )
    rhythm = deepcopy(
        evidence.get("rhythmFeatures")
        or {}
    )

    return {
        "source": "complete_episode_pack",
        "diagnosis": diagnosis,
        "ventricularRateBpm": rhythm.get(
            "ventricularRateBpm"
        ),
        "atrialRateBpm": rhythm.get(
            "atrialRateBpm"
        ),
        "qrsDurationMs": rhythm.get(
            "qrsDurationMs"
        ),
        "qtcMs": rhythm.get("qtcMs"),
        "prIntervalMs": rhythm.get(
            "prIntervalMs"
        ),
        "regularity": rhythm.get("regularity"),
        "axisDegrees": rhythm.get(
            "axisDegrees"
        ),
        "pWavePresent": rhythm.get(
            "pWavePresent"
        ),
        "atrialActivityPresent": rhythm.get(
            "atrialActivityPresent"
        ),
        "atrioventricularAssociation": rhythm.get(
            "atrioventricularAssociation"
        ),
        "findings": deepcopy(
            rhythm.get("findings") or []
        ),
    }


def _episode_pack_context(
    evidence: dict[str, Any],
) -> dict[str, Any]:
    raw = _complete_episode_pack(evidence)

    patient = deepcopy(
        raw.get("patient")
        or evidence.get("patientContext")
        or {}
    )
    patient.pop("disclaimer", None)

    raw_ecg = deepcopy(raw.get("ecg") or {})
    raw_measurements = deepcopy(
        raw_ecg.get("measurements") or {}
    )

    return {
        "available": True,
        "source": "complete_episode_pack",
        "schemaVersion": raw.get("schemaVersion"),
        "episodeId": raw.get("episodeId"),
        "patient": patient,
        "episode": deepcopy(raw.get("episode") or {}),
        "structuralHeart": deepcopy(
            evidence.get("structuralHeartContext") or {}
        ),
        "hemodynamics": deepcopy(
            evidence.get("hemodynamicStatus") or {}
        ),
        "vitals": deepcopy(
            raw.get("vitals")
            or evidence.get("episodeTimeVitals")
            or {}
        ),
        "labs": deepcopy(
            raw.get("labs")
            or evidence.get("laboratoryContext")
            or {}
        ),
        "electrolytes": deepcopy(
            evidence.get("electrolyteContext") or {}
        ),
        "renal": deepcopy(
            evidence.get("renalContext") or {}
        ),
        "ischemia": deepcopy(
            evidence.get("ischemiaContext") or {}
        ),
        "infection": deepcopy(
            evidence.get("infectionContext") or {}
        ),
        "qt": deepcopy(
            evidence.get("qtContext") or {}
        ),
        "medications": deepcopy(
            raw.get("medications")
            or evidence.get("medicationContext")
            or {}
        ),
        "infusions": deepcopy(
            raw.get("infusions")
            or (
                (evidence.get("medicationContext") or {}).get("infusions")
                or []
            )
        ),
        "toxicity": deepcopy(
            evidence.get("toxicityContext") or {}
        ),
        "clinicalContext": deepcopy(
            raw.get("clinicalContext")
            or evidence.get("recentClinicalContext")
            or {}
        ),
        "ecgMeasurements": (
            raw_measurements
            or deepcopy(evidence.get("rhythmFeatures") or {})
        ),
    }


def _controlled_event_context(
    pack: dict[str, Any],
) -> dict[str, Any]:
    labs = pack.get("labs") or {}

    metabolic: dict[str, Any] = {}
    for name in (
        "lactate",
        "ph",
        "bicarbonate",
        "glucose",
    ):
        if name in labs:
            metabolic[name] = deepcopy(
                labs.get(name)
            )

    return {
        "included": True,
        "source": "complete_episode_pack",
        "hemodynamics": deepcopy(
            pack.get("hemodynamics") or {}
        ),
        "ischemia": deepcopy(
            pack.get("ischemia") or {}
        ),
        "electrolytes": deepcopy(
            pack.get("electrolytes") or {}
        ),
        "infection": deepcopy(
            pack.get("infection") or {}
        ),
        "renal": deepcopy(
            pack.get("renal") or {}
        ),
        "qt": deepcopy(
            pack.get("qt") or {}
        ),
        "toxicity": deepcopy(
            pack.get("toxicity") or {}
        ),
        "metabolic": metabolic,
    }


def _episode(
    evidence: dict[str, Any],
    deterministic: dict[str, Any],
) -> dict[str, Any]:
    capture = evidence.get("capture") or {}

    return {
        "episodeId": evidence.get("episodeId"),
        "incidentId": evidence.get(
            "incidentId"
        ),
        "scenarioId": evidence.get(
            "scenarioId"
        ),
        "demoRunId": evidence.get(
            "demoRunId"
        ),
        "mode": "evaluation_injection",
        "clinicalContextMode": PROMPT_MODE,
        "waveformSource": "physionet-incart",
        "sampleRateHz": (
            capture.get("sampleRateHz")
            or capture.get("sampleRate")
        ),
        "leads": deepcopy(
            capture.get("leads") or []
        ),
        "captureSeconds": capture.get(
            "durationSeconds"
        ),
        "preEventSeconds": capture.get(
            "preSeconds"
        ),
        "eventSeconds": capture.get(
            "eventSeconds"
        ),
        "postEventSeconds": capture.get(
            "postSeconds"
        ),
        "captureComplete": capture.get(
            "complete"
        ),
        "signalValidationPassed": bool(
            (
                deterministic.get(
                    "signalQuality"
                )
                or {}
            ).get("status")
            == "ready"
        ),
    }


def _measurement_conflicts(
    controlled: dict[str, Any],
    deterministic: dict[str, Any],
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []

    independent_rhythm = (
        deterministic.get("rhythm") or {}
    )
    independent_qrs = (
        deterministic.get("qrs") or {}
    )

    controlled_rate = _finite(
        controlled.get(
            "ventricularRateBpm"
        )
    )
    independent_rate = _finite(
        independent_rhythm.get(
            "medianHeartRateBpm"
        )
    )

    if (
        controlled_rate is not None
        and independent_rate is not None
    ):
        difference = abs(
            controlled_rate - independent_rate
        )
        threshold = max(
            10.0,
            abs(controlled_rate) * 0.15,
        )

        if difference > threshold:
            conflicts.append(
                {
                    "id": "heart_rate_conflict",
                    "material": True,
                    "controlledValue": (
                        controlled_rate
                    ),
                    "independentValue": (
                        independent_rate
                    ),
                    "unit": "bpm",
                    "difference": round(
                        difference,
                        3,
                    ),
                    "requiredAcknowledgement": (
                        "State that the episode-pack "
                        "rate and independent Phase 6 "
                        "median rate differ without "
                        "changing the fixed diagnosis."
                    ),
                }
            )

    controlled_qrs = _finite(
        controlled.get("qrsDurationMs")
    )
    independent_qrs_value = _finite(
        independent_qrs.get(
            "medianTriggerQrsDurationMilliseconds"
        )
        or independent_qrs.get(
            "medianTriggerQrsMs"
        )
    )

    if (
        controlled_qrs is not None
        and independent_qrs_value
        is not None
    ):
        difference = abs(
            controlled_qrs
            - independent_qrs_value
        )

        if difference > 25.0:
            conflicts.append(
                {
                    "id": "qrs_duration_conflict",
                    "material": True,
                    "controlledValue": (
                        controlled_qrs
                    ),
                    "independentValue": (
                        independent_qrs_value
                    ),
                    "unit": "ms",
                    "difference": round(
                        difference,
                        3,
                    ),
                    "requiredAcknowledgement": (
                        "State that episode-pack and "
                        "Phase 6 QRS measurements differ."
                    ),
                }
            )

    return conflicts


def _coverage_requirements(
    evidence: dict[str, Any],
) -> list[dict[str, Any]]:
    requirements = []

    for raw in (
        evidence.get("coverageRequirements")
        or []
    ):
        if not isinstance(raw, dict):
            continue

        identifier = _text(raw.get("id"))
        if identifier == "oracleClinicalContext":
            continue

        item = deepcopy(raw)

        if identifier == "etiologySupport":
            item["instruction"] = (
                "State the most likely etiology using "
                "the complete episode-pack evidence."
            )

        requirements.append(item)

    if not any(
        item.get("id")
        == "episodePackClinicalContext"
        for item in requirements
    ):
        requirements.append(
            {
                "id": "episodePackClinicalContext",
                "instruction": (
                    "Use the episode-pack patient, "
                    "history, vitals, labs, medications, "
                    "and clinical context when relevant."
                ),
                "requiredInResponse": False,
                "matchTerms": [
                    "history",
                    "vital",
                    "laboratory",
                    "medication",
                    "clinical context",
                    "patient",
                ],
                "matchAllGroups": [],
                "evidencePaths": [
                    "episodePackContext"
                ],
            }
        )

    return requirements


def _numeric_evidence(
    value: Any,
    *,
    path: str = "",
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []

    if isinstance(value, dict):
        for key, item in value.items():
            child = (
                f"{path}.{key}"
                if path
                else str(key)
            )
            output.extend(
                _numeric_evidence(
                    item,
                    path=child,
                )
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child = f"{path}[{index}]"
            output.extend(
                _numeric_evidence(
                    item,
                    path=child,
                )
            )
    else:
        number = _finite(value)
        if number is not None:
            output.append(
                {
                    "value": number,
                    "path": path,
                }
            )

    return output


def build_episode_pack_only_evidence(
    evidence_bundle: dict[str, Any],
    *,
    phase7_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build the exact evidence visible to both the SLM and deterministic validator.

    Oracle SMART remains an authentication and scenario-routing mechanism only.
    No Oracle FHIR demographics, observations, medications, conditions,
    encounters, reports, or documents are copied into this envelope.
    """
    if (
        evidence_bundle.get("schemaVersion")
        == V4_SCHEMA
        and evidence_bundle.get(
            "clinicalPromptMode"
        )
        == PROMPT_MODE
    ):
        return evidence_bundle

    deterministic = _phase6(
        phase7_evidence
    )
    controlled = _controlled_rhythm(
        evidence_bundle
    )
    pack = _episode_pack_context(
        evidence_bundle
    )
    requirements = _coverage_requirements(
        evidence_bundle
    )

    envelope: dict[str, Any] = {
        "schemaVersion": V4_SCHEMA,
        "clinicalPromptMode": PROMPT_MODE,
        "task": {
            "taskType": (
                "episode_contextual_interpretation"
            ),
            "fixedDiagnosisMustBePreserved": True,
            "independentRhythmDiagnosisAllowed": False,
            "treatmentRecommendationAllowed": False,
            "causalCertaintyRequired": False,
        },
        "patientLinkage": {
            "samePatientVerified": None,
            "linkageMode": (
                "oracle_launch_only"
            ),
            "oracleClinicalContextUsed": False,
            "warnings": [],
        },
        "sourceManifest": {
            "patientContextSource": (
                "complete_episode_pack"
            ),
            "episodeContextSource": (
                "complete_episode_pack"
            ),
            "controlledEventSource": (
                "complete_episode_pack"
            ),
            "waveformAnalysisSource": (
                "phase6_deterministic_analysis"
            ),
            "detectorSource": (
                "evaluation_detector"
            ),
            "oracleSmartRole": (
                "authentication_and_scenario_routing_only"
            ),
            "oracleFhirClinicalContextUsed": False,
            "benchmarkSource": (
                "hidden_scenario_answer_key"
            ),
        },
        "episode": _episode(
            evidence_bundle,
            deterministic,
        ),
        "controlledRhythm": controlled,
        "controlledEventContext": (
            _controlled_event_context(pack)
        ),
        "episodePackContext": pack,
        "detector": deepcopy(
            evidence_bundle.get("detector")
            or {}
        ),
        "deterministicAnalysis": (
            deterministic
        ),
        "measurementConflicts": (
            _measurement_conflicts(
                controlled,
                deterministic,
            )
        ),
        "oracleContext": {
            "available": False,
            "source": "oracle_smart_fhir",
            "excludedByPolicy": True,
            "clinicalContextUsed": False,
            "reason": (
                "episode_pack_only_mode"
            ),
        },
        "missingEvidence": [
            deepcopy(item)
            for item in (
                evidence_bundle.get("missingEvidence")
                or []
            )
            if isinstance(item, dict)
            and "oracle" not in json.dumps(
                item,
                ensure_ascii=False,
            ).lower()
            and "fhir" not in json.dumps(
                item,
                ensure_ascii=False,
            ).lower()
        ],
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
        "coverageRequirements": requirements,
        "modelContextMode": PROMPT_MODE,
        "benchmarkContextComplete": True,
        "benchmarkAlignmentMode": (
            "full_scenario"
        ),
    }

    envelope["episodePackFingerprint"] = _fingerprint(
        envelope.get("episodePackContext") or {}
    )

    pack_text = json.dumps(
        envelope.get("episodePackContext") or {},
        ensure_ascii=False,
    ).lower()
    forbidden_markers = (
        "pairedoraclepatientcontext",
        "oraclepatientcontext",
        "oracle_smart_fhir",
        "oracle smart launch",
        "smart, wilma",
        "fhirbaseurl",
        "fhir_base_url",
        "access_token",
        "refresh_token",
        "bearer ",
    )
    leaked = [
        marker
        for marker in forbidden_markers
        if marker in pack_text
    ]
    if leaked:
        raise ValueError(
            "Episode-pack-only evidence contains forbidden Oracle/FHIR "
            "clinical content: "
            + ", ".join(leaked)
        )

    fingerprint_source = deepcopy(
        envelope
    )
    fingerprint = _fingerprint(
        fingerprint_source
    )

    required = [
        item.get("id")
        for item in requirements
        if item.get(
            "requiredInResponse"
        )
        is True
    ]
    optional = [
        item.get("id")
        for item in requirements
        if item.get(
            "requiredInResponse"
        )
        is not True
    ]

    contract = {
        "schemaVersion": VALIDATOR_SCHEMA,
        "clinicalPromptMode": PROMPT_MODE,
        "evidenceFingerprint": fingerprint,
        "requiredCoverage": required,
        "optionalCoverage": optional,
        "coverageRequirements": requirements,
        "forbiddenClaims": [
            "independent_competing_rhythm",
            "treatment_recommendation",
            "unsupported_patient_fact",
            "unsupported_numeric_claim",
            "oracle_fhir_clinical_fact",
            "phase6_as_diagnosis",
        ],
        "numericEvidence": _numeric_evidence(
            fingerprint_source
        ),
        "temporalRules": [],
        "causalRules": [
            (
                "Use only causal relationships "
                "supported by the complete episode pack."
            ),
            (
                "Do not introduce Oracle FHIR facts "
                "or external patient history."
            ),
        ],
        "sourceRules": [
            (
                "The complete episode pack owns all "
                "patient and clinical context."
            ),
            (
                "Oracle SMART is launch and routing "
                "only; Oracle FHIR clinical context "
                "is excluded."
            ),
            (
                "The controlled diagnosis is fixed."
            ),
            (
                "Phase 6 supplies measurements and "
                "limitations, not diagnosis."
            ),
            (
                "The SLM supplies etiology and context "
                "only."
            ),
        ],
    }

    envelope["evidenceFingerprint"] = (
        fingerprint
    )
    envelope["validatorContract"] = (
        contract
    )

    return envelope
