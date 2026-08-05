from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Any

from .model_clinical_evidence import build_model_clinical_evidence


LEGACY_SYSTEM_PROMPT = """
You are a clinical etiology and episode-context summarizer.

The ECG event diagnosis is supplied by an upstream controlled evaluation source.
Treat that diagnosis as fixed. Do not diagnose, reclassify, or provide a competing
rhythm differential.

For an Oracle SMART-bound run, use Oracle SMART FHIR as the only source of
patient identity, demographics, laboratory results, vital signs, medications,
conditions, encounters, reports, and documents. Do not use the fictional source
scenario patient, source scenario laboratory panel, or source scenario medical
history as facts about the Oracle patient.

The controlled event block supplies only the event waveform label, rhythm
measurements, detector timing, and capture timing. The Oracle block supplies the
patient context. Keep those responsibilities separate.

Use only the supplied evidence. Do not invent symptoms, history, medications,
laboratory values, vital signs, procedures, treatment events, or numeric values.
If the Oracle context does not support a specific cause, state that the etiology
is not established from the supplied context instead of guessing.

Your response is limited to:
1. episodeSummary
2. detectedEpisodeContext
3. mostLikelyEtiology
4. contributingFactors
5. uncertaintyAndMissingData

Do not provide treatment recommendations, medication advice, procedures,
management plans, or next steps. Return clinical JSON content only.
""".strip()


V4_SYSTEM_PROMPT = """
You are a clinical episode-context and etiology summarizer.

Use only the evidence printed in this request. The validator uses the same scoped
evidence, so do not rely on outside knowledge to invent case facts.

Source ownership:
- CONTROLLED RHYTHM supplies the fixed event diagnosis and controlled rhythm facts.
- CONTROLLED EVENT CONTEXT, when present, supplies synthetic event-specific facts.
- PHASE 6 supplies independent waveform measurements and limitations.
- ORACLE SMART FHIR supplies patient context. Oracle data does not own the rhythm diagnosis.

Rules:
1. Preserve the fixed diagnosis. Do not diagnose, reclassify, or offer a competing rhythm.
2. Keep controlled-event, Phase 6, and Oracle facts distinguishable.
3. Missing data is not a normal or negative finding. Say "not supplied" or "not established."
4. Historical or remote Oracle data must be described with its timing; never call it current.
5. Medication orders are not administration, exposure, adherence, or causation.
6. Do not claim that remote Oracle data caused the controlled event.
7. Acknowledge every material measurement conflict printed in the request.
8. Do not provide treatment recommendations, medication advice, procedures, or management plans.
9. Safe rounding is allowed, but do not change the clinical magnitude or unit.
10. Scope every negative statement to its source. Prefer "controlled-event evidence did not support X" over "the patient has no X."
11. When only some electrolytes are supplied, name those analytes. Say "supplied potassium and magnesium were within reference" rather than "no electrolyte abnormalities."
12. An empty Oracle condition list means no conditions were returned; it does not prove that disease is absent.
13. Never call ventricular fibrillation pulseless electrical activity. Preserve the fixed rhythm terminology.

Return only these JSON fields:
- episodeSummary: string
- detectedEpisodeContext: string
- mostLikelyEtiology: string
- contributingFactors: array of strings
- uncertaintyAndMissingData: array of strings
""".strip()

EPISODE_PACK_SYSTEM_PROMPT = """
You are a clinical episode etiology summarizer.

Use only the clinical evidence supplied in this request. Treat the episode
label as established clinical context. Explain what happened, the most likely
etiology, and the clinical evidence connecting that etiology to the episode.

Do not discuss technical implementation, data transport, capture mechanics,
validation, storage, provenance, or the generation process.

Clinical reasoning rules:
1. Do not reclassify, replace, or independently diagnose the supplied episode
   label.

2. Use the complete episode context, including relevant history, symptoms,
   recent events, hemodynamics, ECG and morphology, laboratory values,
   medication exposures, and valid Phase 6 measurements.

3. Use Phase 6 measurements only when they are valid, clinically
   interpretable, and relevant to the etiologic explanation. Do not mention a
   Phase 6 value merely because it was supplied.

4. Do not invent facts, diagnoses, laboratory values, medication exposures,
   causal mechanisms, or temporal relationships.

5. Do not treat omitted information as a negative or normal finding.

6. Do not mention routine missing values or implementation-related
   limitations.

7. Keep the episode summary concise. Describe the event and immediate clinical
   state without explaining the full etiology.

8. In mostLikelyEtiologyAndClinicalContext, state the leading etiology first.
   Then connect it to the strongest supplied evidence. Do not repeat the
   episode summary.

9. contributingFactors must contain only supported causal, precipitating, or predisposing
   factors that help explain why the episode occurred.

10. Do not use the following as contributing factors unless the item itself is
    a causal exposure or physiologic trigger:
    - the leading etiology repeated under another name,
    - diagnostic biomarkers,
    - ECG findings used only to recognize the episode,
    - symptoms used only as evidence,
    - hemodynamic status,
    - treatment already in progress,
    - consequences occurring after the episode.

11. Examples of appropriate contributing factors include:
    - pre-existing structural heart disease,
    - prior myocardial scar,
    - medication exposure or toxicity,
    - impaired medication clearance,
    - electrolyte depletion,
    - missed dialysis,
    - infection or systemic inflammatory stress,
    - a supported physiologic or autonomic trigger.

12. Include materialEtiologicUncertainty only when the supplied evidence leaves
    a clinically meaningful causal mechanism unresolved, when two plausible
    etiologies remain, or when a missing or conflicting fact could materially
    alter the leading conclusion.

13. Return an empty materialEtiologicUncertainty array when one leading
    etiology is adequately supported and no clinically meaningful alternative
    mechanism remains unresolved.

14. Do not use materialEtiologicUncertainty to list routine unavailable laboratory
    values, technical limitations, data-source information, or
    general disclaimers.

15. When the evidence establishes a syndrome but cannot distinguish between
    clinically meaningful mechanisms, state that uncertainty concisely. For
    example, if a re-entrant PSVT is supported but the evidence cannot
    distinguish AVNRT from AVRT, state that distinction as material uncertainty.

16. Return one to five concise contributing factors and no more than two
    material uncertainty statements.

17. Do not provide treatment recommendations, management plans, next steps,
    medication advice, procedures, or action items.

18. Return valid JSON only.

Return exactly these four model-owned content fields:
- episodeSummary
- mostLikelyEtiologyAndClinicalContext
- contributingFactors
- materialEtiologicUncertainty
""".strip()



def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value).strip()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in {float("inf"), float("-inf")} else None


def _short_date(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text[:10]


def _human_relation(item: dict[str, Any]) -> str:
    label = _text(item.get("latestRelationLabel") or item.get("relationLabel"))
    if label:
        parts = label.lower().replace("minutes", "min").split()
        try:
            amount = float(parts[0])
        except (ValueError, IndexError):
            return label
        direction = "after" if "after" in parts else "before"
        if amount < 120:
            return f"{amount:.0f} min {direction} event"
        if amount < 60 * 48:
            return f"{amount / 60:.1f} h {direction} event"
        return f"{amount / 1440:.1f} d {direction} event"

    minutes = _finite(item.get("minutesFromAnchor"))
    if minutes is None:
        return "time relative to event unavailable"
    absolute = abs(minutes)
    direction = "after" if minutes > 0 else "before"
    if absolute < 120:
        return f"{absolute:.0f} min {direction} event"
    if absolute < 60 * 48:
        return f"{absolute / 60:.1f} h {direction} event"
    return f"{absolute / 1440:.1f} d {direction} event"


def _section(title: str, lines: list[str]) -> list[str]:
    clean = [line for line in lines if line]
    return [title, *clean, ""] if clean else []


def _mapping_lines(value: dict[str, Any], *, prefix: str = "") -> list[str]:
    lines: list[str] = []
    for key, item in value.items():
        if key in {"available", "source"} or item in (None, "", [], {}):
            continue
        label = f"{prefix}{key}" if prefix else key
        if isinstance(item, dict):
            nested = [
                f"{nested_key}={_text(nested_value)}"
                for nested_key, nested_value in item.items()
                if nested_value not in (None, "", [], {})
            ]
            if nested:
                lines.append(f"- {label}: " + "; ".join(nested))
        elif isinstance(item, list):
            values = [_text(entry) for entry in item if _text(entry)]
            if values:
                lines.append(f"- {label}: " + "; ".join(values[:10]))
        else:
            lines.append(f"- {label}: {_text(item)}")
    return lines


def _legacy_event_lines(evidence: dict[str, Any]) -> list[str]:
    diagnosis = evidence.get("authoritativeDiagnosis") or {}
    rhythm = evidence.get("rhythmFeatures") or {}
    detector = evidence.get("detector") or {}
    capture = evidence.get("capture") or {}
    lines = [
        f"- diagnosis: {_text(diagnosis.get('display'))}",
        f"- diagnosisCode: {_text(diagnosis.get('code'))}",
    ]
    for key in (
        "ventricularRateBpm", "atrialRateBpm", "qrsDurationMs", "qtcMs",
        "prIntervalMs", "regularity", "axisDegrees", "pWavePresent",
        "atrialActivityPresent", "atrioventricularAssociation",
    ):
        value = rhythm.get(key)
        if value not in (None, ""):
            lines.append(f"- {key}: {_text(value)}")
    findings = [_text(item) for item in rhythm.get("findings") or [] if _text(item)]
    if findings:
        lines.append("- findings: " + "; ".join(findings[:10]))
    for key in (
        "ruleId", "estimatedRateBpm", "triggerLatencySeconds",
        "referenceOnsetOffsetSeconds", "detectedTriggerOffsetSeconds",
    ):
        value = detector.get(key)
        if value not in (None, ""):
            lines.append(f"- detector.{key}: {_text(value)}")
    for key in ("durationSeconds", "preSeconds", "eventSeconds", "postSeconds", "complete"):
        value = capture.get(key)
        if value not in (None, ""):
            lines.append(f"- capture.{key}: {_text(value)}")
    return lines



def _legacy_phase6_lines(
    evidence: dict[str, Any],
) -> list[str]:
    captured = (
        evidence.get(
            "capturedDeterministicAnalysis"
        )
        or {}
    )
    measured = (
        captured.get(
            "independentlyMeasuredEcg"
        )
        or captured
    )

    windowed = (
        captured.get(
            "windowedAnalysis"
        )
        or measured.get(
            "windowedAnalysis"
        )
        or captured.get(
            "phase6WindowedAnalysis"
        )
        or {}
    )

    if (
        windowed.get(
            "schemaVersion"
        )
        == "phase6-windowed-analysis-v1"
    ):
        heart_rate = (
            windowed.get(
                "heartRate"
            )
            or {}
        )
        qrs = (
            windowed.get("qrs")
            or {}
        )
        morphology = (
            windowed.get(
                "morphology"
            )
            or {}
        )
        confidence = (
            windowed.get(
                "confidence"
            )
            or {}
        )
        windows = (
            windowed.get(
                "measurementWindows"
            )
            or {}
        )

        lines = [
            (
                "- schemaVersion: "
                + _text(
                    windowed.get(
                        "schemaVersion"
                    )
                )
            ),
            (
                "- analysisStatus: "
                + _text(
                    windowed.get(
                        "analysisStatus"
                    )
                )
            ),
        ]

        for key, window in (
            windows.items()
        ):
            if not isinstance(
                window,
                dict,
            ):
                continue

            lines.append(
                (
                    f"- window.{key}: "
                    f"{_text(window.get('startSeconds'))}"
                    " to "
                    f"{_text(window.get('endSeconds'))}"
                    " seconds"
                )
            )

        for label, value in (
            (
                "fullCaptureMedianHeartRateBpm",
                heart_rate.get(
                    "fullCaptureMedianBpm"
                ),
            ),
            (
                "preEventMedianHeartRateBpm",
                heart_rate.get(
                    "preEventMedianBpm"
                ),
            ),
            (
                "controlledEventMedianHeartRateBpm",
                heart_rate.get(
                    "eventMedianBpm"
                ),
            ),
            (
                "controlledEventHeartRateRangeBpm",
                heart_rate.get(
                    "eventRangeBpm"
                ),
            ),
            (
                "controlledEventHeartRateValid",
                heart_rate.get(
                    "eventMeasurementValid"
                ),
            ),
            (
                "controlledEventHeartRateConfidence",
                heart_rate.get(
                    "eventConfidenceGrade"
                ),
            ),
            (
                "preEventMedianQrsMs",
                qrs.get(
                    "preEventMedianMs"
                ),
            ),
            (
                "controlledEventMedianQrsMs",
                qrs.get(
                    "eventMedianMs"
                ),
            ),
            (
                "postEventMedianQrsMs",
                qrs.get(
                    "postEventMedianMs"
                ),
            ),
            (
                "controlledEventQrsValid",
                qrs.get(
                    "eventMeasurementValid"
                ),
            ),
            (
                "controlledEventQrsConfidence",
                qrs.get(
                    "eventConfidenceGrade"
                ),
            ),
            (
                "eventVsPreDifferenceScore",
                morphology.get(
                    "eventVsPreDifferenceScore"
                ),
            ),
            (
                "eventVsPostDifferenceScore",
                morphology.get(
                    "eventVsPostDifferenceScore"
                ),
            ),
            (
                "leadAgreementScore",
                morphology.get(
                    "leadAgreementScore"
                ),
            ),
            (
                "windowedMeasurementConfidenceScore",
                confidence.get(
                    "score"
                ),
            ),
            (
                "windowedMeasurementConfidenceGrade",
                confidence.get(
                    "grade"
                ),
            ),
        ):
            if value not in (
                None,
                "",
            ):
                lines.append(
                    f"- {label}: "
                    f"{_text(value)}"
                )

        for limitation in (
            windowed.get(
                "limitations"
            )
            or []
        ):
            if _text(limitation):
                lines.append(
                    "- limitation: "
                    + _text(
                        limitation
                    )
                )

        return lines

    reference = (
        captured.get(
            "datasetReference"
        )
        or measured.get(
            "datasetReference"
        )
        or {}
    )
    rhythm = (
        measured.get("rhythm")
        or {}
    )
    qrs = measured.get("qrs") or {}
    morphology = (
        measured.get(
            "morphology"
        )
        or {}
    )
    agreement = (
        measured.get(
            "leadAgreement"
        )
        or {}
    )
    confidence = (
        measured.get(
            "confidence"
        )
        or {}
    )
    candidate = (
        measured.get(
            "independentCandidateDetection"
        )
        or {}
    )
    lines: list[str] = []

    for label, value in (
        (
            "analysisStatus",
            captured.get(
                "analysisStatus"
            ),
        ),
        (
            "legacyFullCaptureMedianHeartRateBpm",
            rhythm.get(
                "medianHeartRateBpm"
            ),
        ),
        (
            "legacyFullCaptureHeartRateRangeBpm",
            rhythm.get(
                "heartRateRangeBpm"
            ),
        ),
        (
            "legacyMedianTriggerQrsMs",
            qrs.get(
                "medianTriggerQrsDurationMilliseconds"
            ),
        ),
        (
            "legacyMedianBaselineQrsMs",
            qrs.get(
                "medianBaselineQrsDurationMilliseconds"
            ),
        ),
        (
            "legacyMorphologyDifferenceScore",
            morphology.get(
                "medianDifferenceScore"
            ),
        ),
        (
            "legacyLeadAgreementScore",
            agreement.get(
                "medianScore"
            ),
        ),
        (
            "legacyMeasurementConfidenceScore",
            confidence.get(
                "score"
            ),
        ),
        (
            "legacyMeasurementConfidenceGrade",
            confidence.get(
                "grade"
            ),
        ),
        (
            "independentAbnormalMorphologyCandidates",
            candidate.get(
                "abnormalMorphologyCandidateCount"
            ),
        ),
    ):
        if value not in (
            None,
            "",
        ):
            lines.append(
                f"- {label}: "
                f"{_text(value)}"
            )

    lines.append(
        (
            "- limitation: Legacy Phase 6 "
            "values are not treated as "
            "controlled-event measurements "
            "unless event-window provenance "
            "is explicitly supplied."
        )
    )

    if reference.get(
        "triggerCounts"
    ):
        lines.append(
            (
                "- datasetReferenceTriggerCounts: "
                + _text(
                    reference.get(
                        "triggerCounts"
                    )
                )
            )
        )

    for limitation in (
        captured.get(
            "limitations"
        )
        or []
    ):
        if _text(limitation):
            lines.append(
                "- limitation: "
                + _text(
                    limitation
                )
            )

    return lines


def _trend_lines(items: list[dict[str, Any]], *, limit: int = 10) -> list[str]:
    output: list[str] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        label = _text(item.get("label") or item.get("field") or "Observation")
        value = item.get("latestValue")
        if value is None:
            continue
        unit = _text(item.get("unit"))
        date = _short_date(item.get("latestAt"))
        relation = _human_relation(item)
        direction = _text(item.get("trendDirection"))
        bucket = _text(item.get("temporalBucket"))
        line = f"- {label}: {_text(value)}" + (f" {unit}" if unit else "")
        details = [part for part in (date, relation, direction, bucket) if part]
        if details:
            line += " (" + "; ".join(details) + ")"
        output.append(line)
    return output


def _medication_lines(items: list[dict[str, Any]], *, limit: int = 10) -> list[str]:
    output: list[str] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name") or item.get("medication") or item.get("display"))
        if not name:
            continue
        evidence = _text(item.get("evidenceType") or item.get("evidenceLevel") or item.get("resourceType") or "record")
        status = _text(item.get("status"))
        date = _short_date(item.get("eventTime") or item.get("authoredOn"))
        relation = _human_relation(item)
        exposure = _text(item.get("exposureSupported"))
        causal = _text(item.get("causalUseAllowed"))
        details = [part for part in (evidence, status, date, relation) if part]
        details += [f"exposureSupported={exposure}", f"causalUseAllowed={causal}"]
        output.append(f"- {name} (" + "; ".join(details) + ")")
    return output


def _resource_lines(items: list[dict[str, Any]], *, limit: int = 6) -> list[str]:
    output: list[str] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        label = _text(
            item.get("display") or item.get("name") or item.get("type")
            or item.get("description") or item.get("classDisplay") or item.get("status")
        )
        if not label:
            continue
        date = _short_date(item.get("date") or item.get("periodStart") or item.get("recordedDate"))
        output.append(f"- {label}" + (f" ({date})" if date else ""))
    return output


def _oracle_lines(oracle: dict[str, Any]) -> list[str]:
    patient = oracle.get("patient") or {}
    patient_lines = [
        f"- age: {_text(patient.get('age'))}" if patient.get("age") is not None else "",
        f"- sex: {_text(patient.get('sex'))}" if patient.get("sex") else "",
        f"- birthDate: {_text(patient.get('birthDate'))}" if patient.get("birthDate") else "",
        f"- deceased: {_text(patient.get('deceased'))}" if patient.get("deceased") is not None else "",
        f"- maritalStatus: {_text(patient.get('maritalStatus'))}" if patient.get("maritalStatus") else "",
    ]
    languages = patient.get("languages") or []
    if languages:
        patient_lines.append("- languages: " + "; ".join(map(str, languages[:5])))

    lines: list[str] = []
    lines += _section("ORACLE PATIENT DEMOGRAPHICS", patient_lines)
    lines += _section("ORACLE VITAL TRENDS", _trend_lines(list(oracle.get("vitalTrends") or [])))
    lines += _section("ORACLE LAB TRENDS", _trend_lines(list(oracle.get("labTrends") or [])))
    lines += _section("ORACLE MEDICATION EVIDENCE", _medication_lines(list(oracle.get("medications") or oracle.get("medicationTimeline") or [])))
    lines += _section("ORACLE CONDITIONS", _resource_lines(list(oracle.get("conditions") or [])))
    lines += _section("ORACLE ENCOUNTERS", _resource_lines(list(oracle.get("encounters") or []), limit=5))
    lines += _section("ORACLE DIAGNOSTIC REPORTS", _resource_lines(list(oracle.get("diagnosticReports") or []), limit=5))
    lines += _section("ORACLE DOCUMENT METADATA", _resource_lines(list(oracle.get("documentMetadata") or oracle.get("documents") or []), limit=5))
    quality = oracle.get("resourceAvailability") or oracle.get("dataQuality") or {}
    lines += _section("ORACLE RESOURCE AVAILABILITY", _mapping_lines(quality))
    warnings = oracle.get("retrievalWarnings") or []
    lines += _section("ORACLE RETRIEVAL WARNINGS", [f"- {_text(item)}" for item in warnings])
    return lines


def _coverage_lines(evidence: dict[str, Any]) -> list[str]:
    required: list[str] = []
    optional: list[str] = []
    for item in evidence.get("coverageRequirements") or []:
        if not isinstance(item, dict):
            continue
        instruction = _text(item.get("instruction"))
        if not instruction:
            continue
        target = required if item.get("requiredInResponse") is True else optional
        target.append(f"- {item.get('id')}: {instruction}")
    return [
        *_section("REQUIRED CONTENT", required),
        *_section("OPTIONAL CONTENT WHEN RELEVANT", optional),
    ]


def _episode_pack_lines(
    pack: dict[str, Any],
) -> list[str]:
    lines: list[str] = []

    for title, key in (
        ("EPISODE PACK PATIENT", "patient"),
        ("EPISODE PACK EPISODE", "episode"),
        ("EPISODE PACK ECG MEASUREMENTS", "ecgMeasurements"),
        ("EPISODE PACK STRUCTURAL HEART", "structuralHeart"),
        ("EPISODE PACK HEMODYNAMICS", "hemodynamics"),
        ("EPISODE PACK VITALS", "vitals"),
        ("EPISODE PACK LABS", "labs"),
        ("EPISODE PACK ELECTROLYTES", "electrolytes"),
        ("EPISODE PACK RENAL", "renal"),
        ("EPISODE PACK ISCHEMIA", "ischemia"),
        ("EPISODE PACK INFECTION", "infection"),
        ("EPISODE PACK QT", "qt"),
        ("EPISODE PACK MEDICATIONS", "medications"),
        ("EPISODE PACK TOXICITY", "toxicity"),
        ("EPISODE PACK CLINICAL CONTEXT", "clinicalContext"),
    ):
        value = pack.get(key) or {}

        if isinstance(value, dict):
            lines += _section(
                title,
                _mapping_lines(value),
            )
        elif isinstance(value, list):
            lines += _section(
                title,
                [
                    f"- {_text(item)}"
                    for item in value
                    if _text(item)
                ],
            )

    return lines


def _v4_lines(envelope: dict[str, Any]) -> list[str]:
    body: list[str] = []
    episode_pack_only = (
        envelope.get("clinicalPromptMode")
        == "episode_pack_only"
    )

    if not episode_pack_only:
        linkage = envelope.get("patientLinkage") or {}
        body += _section(
            "PATIENT LINKAGE",
            [
                f"- samePatientVerified: {_text(linkage.get('samePatientVerified'))}",
                f"- linkageMode: {_text(linkage.get('linkageMode'))}",
                *[
                    f"- warning: {_text(item)}"
                    for item in linkage.get("warnings") or []
                ],
            ],
        )

    manifest = dict(
        envelope.get("sourceManifest")
        or {}
    )
    if episode_pack_only:
        manifest = {
            key: value
            for key, value in manifest.items()
            if "oracle" not in str(key).lower()
            and "fhir" not in str(key).lower()
            and "oracle" not in _text(value).lower()
            and "fhir" not in _text(value).lower()
        }
    body += _section(
        "SOURCE MANIFEST",
        _mapping_lines(manifest),
    )

    controlled = envelope.get("controlledRhythm") or {}
    diagnosis = controlled.get("diagnosis") or {}
    rhythm_lines = [
        f"- diagnosis: {_text(diagnosis.get('display'))}",
        f"- diagnosisCode: {_text(diagnosis.get('code'))}",
        f"- authoritative: {_text(diagnosis.get('authoritative'))}",
    ]
    for key in (
        "ventricularRateBpm", "atrialRateBpm", "qrsDurationMs", "qtcMs",
        "prIntervalMs", "regularity", "axisDegrees", "pWavePresent",
        "atrialActivityPresent", "atrioventricularAssociation",
    ):
        if controlled.get(key) not in (None, ""):
            rhythm_lines.append(f"- {key}: {_text(controlled.get(key))}")
    findings = controlled.get("findings") or []
    if findings:
        rhythm_lines.append("- findings: " + "; ".join(map(str, findings[:10])))
    body += _section("CONTROLLED RHYTHM", rhythm_lines)

    event = envelope.get("controlledEventContext") or {}
    if event.get("included"):
        for title, key in (
            ("CONTROLLED EVENT HEMODYNAMICS", "hemodynamics"),
            ("CONTROLLED EVENT ISCHEMIA", "ischemia"),
            ("CONTROLLED EVENT ELECTROLYTES", "electrolytes"),
            ("CONTROLLED EVENT INFECTION", "infection"),
            ("CONTROLLED EVENT RENAL", "renal"),
            ("CONTROLLED EVENT QT", "qt"),
            ("CONTROLLED EVENT TOXICITY", "toxicity"),
            ("CONTROLLED EVENT METABOLIC", "metabolic"),
        ):
            value = event.get(key) or {}
            body += _section(title, _mapping_lines(value))

    detector = envelope.get("detector") or {}
    episode = envelope.get("episode") or {}
    body += _section(
        "DETECTOR AND CAPTURE",
        [
            *_mapping_lines(detector),
            f"- captureSeconds: {_text(episode.get('captureSeconds'))}",
            f"- preEventSeconds: {_text(episode.get('preEventSeconds'))}",
            f"- eventSeconds: {_text(episode.get('eventSeconds'))}",
            f"- postEventSeconds: {_text(episode.get('postEventSeconds'))}",
            f"- captureComplete: {_text(episode.get('captureComplete'))}",
        ],
    )

    phase6 = envelope.get("deterministicAnalysis") or {}
    body += _section("PHASE 6 INDEPENDENT ECG", _legacy_phase6_lines({"capturedDeterministicAnalysis": phase6}))

    conflict_lines: list[str] = []

    for item in (
        envelope.get(
            "measurementConflicts"
        )
        or []
    ):
        if not isinstance(
            item,
            dict,
        ):
            continue

        conflict_lines.append(
            (
                "- "
                + _text(
                    item.get("id")
                    or item.get(
                        "metric"
                    )
                )
                + ": controlledEvent="
                + _text(
                    item.get(
                        "controlledEventValue",
                        item.get(
                            "controlledValue"
                        ),
                    )
                )
                + " "
                + _text(
                    item.get("unit")
                )
                + "; phase6Event="
                + _text(
                    item.get(
                        "phase6EventValue",
                        item.get(
                            "independentValue"
                        ),
                    )
                )
                + " "
                + _text(
                    item.get("unit")
                )
                + "; difference="
                + _text(
                    item.get(
                        "difference"
                    )
                )
                + "; confidence="
                + _text(
                    item.get(
                        "confidenceGrade"
                    )
                )
                + "; sameWindow="
                + _text(
                    item.get(
                        "sameWindow"
                    )
                )
                + "; valid="
                + _text(
                    item.get(
                        "measurementValid"
                    )
                )
            )
        )

    body += _section(
        "QUALIFIED MATERIAL MEASUREMENT CONFLICTS",
        conflict_lines,
    )
    body += _section(
        "PHASE 6 COMPARISON LIMITATIONS",
        [
            "- " + _text(item)
            for item in (
                envelope.get(
                    "measurementConflictLimitations"
                )
                or []
            )
            if _text(item)
        ],
    )
    if episode_pack_only:
        body += _episode_pack_lines(
            envelope.get(
                "episodePackContext"
            )
            or {}
        )
    else:
        body += _oracle_lines(
            envelope.get("oracleContext") or {}
        )

    missing_lines = [
        f"- {item.get('id')}: {item.get('reason')} (source={item.get('source')}; negativeFinding={_text(item.get('negativeFinding'))})"
        for item in envelope.get("missingEvidence") or []
        if isinstance(item, dict)
    ]
    body += _section("MISSING EVIDENCE", missing_lines)
    body += _coverage_lines(envelope)
    body += _section(
        "PROMPT METADATA",
        [
            f"- clinicalPromptMode: {_text(envelope.get('clinicalPromptMode'))}",
            f"- evidenceFingerprint: {_text(envelope.get('evidenceFingerprint'))}",
            f"- validatorContractVersion: {_text((envelope.get('validatorContract') or {}).get('schemaVersion'))}",
        ],
    )
    return body


def _legacy_oracle_context_lines(oracle: dict[str, Any]) -> list[str]:
    # Compatibility reader for V2 evidence.
    patient = oracle.get("patient") or {}
    summary = oracle.get("patientSummary") or {}
    minimal = {
        "age": patient.get("age") or summary.get("ageAtContextAnchor"),
        "sex": patient.get("sex") or patient.get("gender") or summary.get("gender"),
        "birthDate": patient.get("dob") or summary.get("birthDate"),
        "deceased": summary.get("deceased"),
        "maritalStatus": summary.get("maritalStatus"),
        "languages": summary.get("languages") or [],
    }
    oracle_copy = dict(oracle)
    oracle_copy["patient"] = minimal
    oracle_copy["medications"] = oracle.get("medicationTimeline") or []
    oracle_copy["documentMetadata"] = oracle.get("documents") or []
    oracle_copy["resourceAvailability"] = oracle.get("dataQuality") or {}
    return _oracle_lines(oracle_copy)


def _scenario_context_lines(evidence: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for title, key in (
        ("PATIENT CONTEXT", "patientContext"),
        ("STRUCTURAL HEART CONTEXT", "structuralHeartContext"),
        ("HEMODYNAMIC CONTEXT", "hemodynamicStatus"),
        ("ELECTROLYTE CONTEXT", "electrolyteContext"),
        ("RENAL CONTEXT", "renalContext"),
        ("ISCHEMIA CONTEXT", "ischemiaContext"),
        ("INFECTION CONTEXT", "infectionContext"),
        ("QT CONTEXT", "qtContext"),
        ("MEDICATION CONTEXT", "medicationContext"),
        ("LABORATORY CONTEXT", "laboratoryContext"),
        ("RECENT CLINICAL CONTEXT", "recentClinicalContext"),
    ):
        value = evidence.get(key)
        if isinstance(value, dict):
            lines += _section(title, _mapping_lines(value))
    return lines


def build_grounded_messages(
    *,
    evidence_bundle: dict[str, Any],
    correction_errors: list[str] | None = None,
    correction_evidence: list[str] | None = None,
) -> list[dict[str, str]]:
    is_v4 = evidence_bundle.get("schemaVersion") == "slm-evidence-envelope-v4"

    episode_pack_only = (
        is_v4
        and evidence_bundle.get("clinicalPromptMode") == "episode_pack_only"
    )

    if is_v4:
        system_prompt = (
            EPISODE_PACK_SYSTEM_PROMPT
            if episode_pack_only
            else V4_SYSTEM_PROMPT
        )
        body = [] if episode_pack_only else _v4_lines(evidence_bundle)
    else:
        system_prompt = LEGACY_SYSTEM_PROMPT
        body: list[str] = []
        body += _section("CONTROLLED ECG EVENT", _legacy_event_lines(evidence_bundle))
        body += _section("CAPTURED DETERMINISTIC ECG MEASUREMENTS", _legacy_phase6_lines(evidence_bundle))
        oracle = evidence_bundle.get("oraclePatientContext") or {}
        if oracle.get("available"):
            body += _legacy_oracle_context_lines(oracle)
            body += [
                "PATIENT-CONTEXT RULE",
                "- Use only the Oracle sections above for patient facts.",
                "- Do not use source-scenario demographics, history, labs, medications, or symptoms as Oracle patient facts.",
                "- If no etiology is directly supported, state that it is not established from the supplied Oracle context.",
                "- If controlled event metadata and independent captured measurements differ, report the distinction without changing the fixed diagnosis.",
                "",
            ]
        else:
            body += _scenario_context_lines(evidence_bundle)
        body += _coverage_lines(evidence_bundle)

    if episode_pack_only:
        model_clinical_evidence = build_model_clinical_evidence(
            evidence_bundle=evidence_bundle,
        )
        evidence_json = json.dumps(
            model_clinical_evidence,
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
        )
        user_content = (
            "CLINICAL EPISODE EVIDENCE\n\n"
            + evidence_json
            + "\n\nTASK\n\n"
            + "Create a concise clinical episode interpretation using only the supplied\n"
              "evidence.\n\n"
            + "OUTPUT REQUIREMENTS\n\n"
            + "- episodeSummary:\n"
              "  Return 2 to 4 concise sentences describing the episode and immediate\n"
              "  clinical state. Do not provide the full etiologic explanation here.\n\n"
            + "- mostLikelyEtiologyAndClinicalContext:\n"
              "  State the leading etiology first, then connect it to the strongest relevant\n"
              "  evidence. Do not repeat the episode summary.\n\n"
            + "- contributingFactors:\n"
              "  Return 1 to 5 concise causal, precipitating, or predisposing factors\n"
              "  supported by the evidence. Do not repeat the leading etiology and do not\n"
              "  list biomarkers, diagnostic ECG findings, symptoms, hemodynamic status,\n"
              "  treatment, or post-event consequences unless the item itself is a causal\n"
              "  exposure or physiologic trigger.\n\n"
            + "- materialEtiologicUncertainty:\n"
              "  Return 0 to 2 concise statements. Return an empty array only when one\n"
              "  leading etiology is adequately supported and no clinically meaningful\n"
              "  causal mechanism remains unresolved. Include a concise statement when the\n"
              "  evidence cannot distinguish between meaningful mechanisms, such as AVNRT\n"
              "  versus AVRT. Do not list routine missing values or technical limitations.\n\n"
            + "Return JSON only."
        )
    else:
        user_content = (
            "CLINICAL EVIDENCE\n\n"
            + "\n".join(body).strip()
        )

    if episode_pack_only and re.search(
        r"\b(?:INCART|API Range|Oracle|FHIR|SMART|reference annotation|"
        r"independent diagnosis|raw ECG arrays?|waveform storage|deduplicat(?:ed|ion)|"
        r"source manifest|evaluation detector|model ownership|generated by the SLM|"
        r"available=no|value=null|measured_unspecified_pattern)\b",
        user_content,
        flags=re.IGNORECASE,
    ):
        raise ValueError(
            "Episode-pack-only prompt contains forbidden implementation or missing-value content."
        )

    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": user_content,
        },
    ]

    if correction_errors:
        evidence_lines = [
            f"- {item}" for item in (correction_evidence or []) if _text(item)
        ]
        correction = [
            "The prior response was rejected. Correct only the issues below using the evidence already supplied:",
            *[f"- {item}" for item in correction_errors],
        ]
        if evidence_lines:
            correction += [
                "",
                "SUPPLIED EVIDENCE FOR THE CORRECTION",
                *evidence_lines,
            ]
        correction += [
            "",
            "SAFE REWRITE RULES",
            "- Correct only the listed errors using the already supplied clinical evidence.",
            "- Do not add omitted facts or routine missing-value statements.",
            "- Keep the established episode label unchanged.",
            "- Keep the combined etiologic explanation concise and evidence-linked.",
            "- Do not add treatment, recommendation, provenance, source-system, or implementation language.",
        ]
        messages.append({"role": "user", "content": "\n".join(correction)})

    return messages
