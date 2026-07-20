from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import settings
from app.incidents import incident_coordinator
from app.slm_widget.validator import (
    parse_model_payload,
    safe_string_list,
    safe_text,
    validated_contributors,
)


DISCLAIMER = (
    "Research prototype only. This interpretation summarizes supplied "
    "evidence and is not an independent diagnosis or treatment recommendation."
)

RECOMMENDED_NEXT_CHECKS = [
    {
        "priority": "high",
        "action": (
            "Review the stored ECG episode windows and deterministic "
            "measurements together."
        ),
        "owner": "clinical_reviewer",
    },
    {
        "priority": "high",
        "action": (
            "Verify episode-near electrolytes, including potassium and "
            "magnesium, when clinically available."
        ),
        "owner": "clinical_reviewer",
    },
    {
        "priority": "medium",
        "action": (
            "Verify medication administration or actual exposure; "
            "a MedicationRequest is an order only."
        ),
        "owner": "clinical_reviewer",
    },
]


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None

    return payload if isinstance(payload, dict) else None


def _safe_incident_id(incident_id: str) -> str:
    value = str(incident_id).strip()

    if not value or ".." in value or "/" in value or "\\" in value:
        raise ValueError("Invalid incident id.")

    return value


def _phase7_dir(incident_id: str) -> Path:
    return (
        Path(settings.INCIDENT_STORAGE_PATH)
        / "phase7"
        / _safe_incident_id(incident_id)
    )


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_or_none(value: Any, digits: int = 2) -> float | None:
    number = _number(value)
    return round(number, digits) if number is not None else None


def _evidence_views(
    evidence: dict[str, Any],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    incident = (
        _dict(evidence.get("incident"))
        or _dict(evidence.get("incidentOverview"))
    )
    ecg = (
        _dict(evidence.get("ecgEvidence"))
        or _dict(
            _dict(evidence.get("evidence")).get(
                "independentlyMeasuredEcg"
            )
        )
    )
    reference = (
        _dict(evidence.get("referenceAnnotations"))
        or _dict(
            _dict(evidence.get("evidence")).get("datasetReference")
        )
        or _dict(ecg.get("referenceAnnotations"))
    )
    temporal = _dict(evidence.get("temporalSummary"))
    current_state = _dict(evidence.get("currentStateEvidence"))
    return incident, ecg, reference, temporal, current_state


def _deterministic_metrics(
    evidence: dict[str, Any],
) -> list[dict[str, Any]]:
    incident, ecg, reference, _temporal, _current = _evidence_views(
        evidence
    )

    rhythm = _dict(ecg.get("rhythm"))
    qrs = _dict(ecg.get("qrs"))
    morphology = _dict(ecg.get("morphology"))
    confidence = (
        _dict(ecg.get("deterministicConfidence"))
        or _dict(ecg.get("confidence"))
    )
    lead_agreement = _dict(ecg.get("leadAgreement"))
    trigger_counts = _dict(reference.get("triggerCounts"))

    raw_metrics = [
        (
            "episodeCount",
            "Episode windows",
            incident.get("episodeCount"),
            None,
            "deterministic_backend",
        ),
        (
            "durationSeconds",
            "Incident duration",
            _round_or_none(incident.get("durationSeconds"), 1),
            "s",
            "deterministic_backend",
        ),
        (
            "referenceVCount",
            "Reference V markers",
            trigger_counts.get("V")
            or reference.get("uniqueReferenceTriggerCount"),
            None,
            "dataset_reference_annotation",
        ),
        (
            "medianHeartRateBpm",
            "Median heart rate",
            _round_or_none(rhythm.get("medianHeartRateBpm"), 1),
            "bpm",
            "deterministic_backend",
        ),
        (
            "triggerQrsMilliseconds",
            "Trigger QRS",
            _round_or_none(
                qrs.get("medianTriggerQrsDurationMilliseconds")
                or qrs.get("triggerQrsDurationMilliseconds"),
                1,
            ),
            "ms",
            "deterministic_backend",
        ),
        (
            "baselineQrsMilliseconds",
            "Baseline QRS",
            _round_or_none(
                qrs.get("medianBaselineQrsDurationMilliseconds")
                or qrs.get("baselineQrsDurationMilliseconds"),
                1,
            ),
            "ms",
            "deterministic_backend",
        ),
        (
            "morphologyDifference",
            "Morphology difference",
            _round_or_none(
                morphology.get("medianDifferenceScore")
                or morphology.get("multiLeadMorphologyScore"),
                3,
            ),
            None,
            "deterministic_backend",
        ),
        (
            "leadAgreement",
            "Lead agreement",
            _round_or_none(
                lead_agreement.get("medianScore")
                or lead_agreement.get("overallScore"),
                1,
            ),
            "%",
            "deterministic_backend",
        ),
        (
            "ecgEvidenceConfidence",
            "ECG evidence confidence",
            _round_or_none(confidence.get("score"), 1),
            "%",
            "deterministic_backend",
        ),
    ]

    return [
        {
            "key": key,
            "label": label,
            "value": value,
            "unit": unit,
            "source": source,
        }
        for key, label, value, unit, source in raw_metrics
        if value is not None
    ]


def _deterministic_narrative(
    evidence: dict[str, Any],
) -> dict[str, Any]:
    incident, ecg, reference, temporal, current_state = _evidence_views(
        evidence
    )

    display = incident.get("display") or "Captured ECG incident"
    episode_count = int(_number(incident.get("episodeCount")) or 0)
    duration = _round_or_none(incident.get("durationSeconds"), 1)

    rhythm = _dict(ecg.get("rhythm"))
    qrs = _dict(ecg.get("qrs"))
    morphology = _dict(ecg.get("morphology"))

    median_hr = _round_or_none(rhythm.get("medianHeartRateBpm"), 1)
    hr_range = _list(rhythm.get("heartRateRangeBpm"))

    trigger_qrs = _round_or_none(
        qrs.get("medianTriggerQrsDurationMilliseconds")
        or qrs.get("triggerQrsDurationMilliseconds"),
        1,
    )
    baseline_qrs = _round_or_none(
        qrs.get("medianBaselineQrsDurationMilliseconds")
        or qrs.get("baselineQrsDurationMilliseconds"),
        1,
    )
    morphology_score = _round_or_none(
        morphology.get("medianDifferenceScore")
        or morphology.get("multiLeadMorphologyScore"),
        3,
    )
    reference_count = (
        _dict(reference.get("triggerCounts")).get("V")
        or reference.get("uniqueReferenceTriggerCount")
    )

    episode_narrative = (
        f"The incident groups {episode_count} stored episode window"
        f"{'' if episode_count == 1 else 's'}"
        + (
            f" across {duration:g} seconds."
            if duration is not None
            else "."
        )
    )

    rhythm_parts = [
        "The ECG findings shown here come from deterministic backend "
        "measurements."
    ]

    if median_hr is not None:
        rhythm_parts.append(
            f"Median measured heart rate was {median_hr:g} bpm."
        )

    if len(hr_range) >= 2:
        low = _round_or_none(hr_range[0], 1)
        high = _round_or_none(hr_range[1], 1)
        if low is not None and high is not None:
            rhythm_parts.append(
                f"The measured range was {low:g} to {high:g} bpm."
            )

    if reference_count is not None:
        rhythm_parts.append(
            f"The dataset contains {reference_count} reference V marker(s); "
            "these are reference annotations, not an independent diagnosis."
        )

    morphology_parts = []

    if trigger_qrs is not None and baseline_qrs is not None:
        morphology_parts.append(
            f"Median trigger QRS was {trigger_qrs:g} ms compared with "
            f"{baseline_qrs:g} ms for the baseline reference."
        )

    if morphology_score is not None:
        morphology_parts.append(
            f"The multi-episode morphology difference score was "
            f"{morphology_score:g}."
        )

    if not morphology_parts:
        morphology_parts.append(
            "Morphology measurements are unavailable or incomplete."
        )

    latest_age = (
        current_state.get("latestEvidenceAgeMinutes")
        or temporal.get("closestVitalMinutes")
        or temporal.get("closestVitalObservationMinutesFromAnchor")
    )

    return {
        "headline": display,
        "episodeNarrative": episode_narrative,
        "arrhythmiaNarrative": " ".join(rhythm_parts),
        "morphologyNarrative": " ".join(morphology_parts),
        "currentSituation": {
            "status": (
                current_state.get("status")
                or "insufficient_current_data"
            ),
            "narrative": (
                "Current rhythm persistence and hemodynamic status are "
                "unknown because continuous episode-near supporting "
                "evidence was not supplied."
            ),
            "ongoingArrhythmiaStatus": (
                current_state.get("ongoingArrhythmia")
                or current_state.get("ongoingArrhythmiaStatus")
                or "unknown"
            ),
            "hemodynamicStatus": (
                current_state.get("hemodynamicStatus") or "unknown"
            ),
            "latestSupportingEvidenceAgeMinutes": _round_or_none(
                latest_age,
                1,
            ),
        },
        "rootCauseNarrative": (
            "No root cause is confirmed. Historical or remote FHIR records "
            "may be displayed as background only and must not be treated as "
            "episode-time causal evidence."
        ),
    }


def _extract_widget_narrative(
    model_payload: dict[str, Any],
) -> dict[str, Any]:
    widget = _dict(model_payload.get("widgetInterpretation"))
    if not widget:
        widget = _dict(model_payload.get("modelNarrative"))
    if widget:
        return widget

    return {
        "episodeNarrative": model_payload.get("evidenceSummary"),
        "arrhythmiaNarrative": " ".join(
            safe_string_list(
                model_payload.get("ecgFindings"),
                maximum=4,
                allow_ecg_terms=True,
            )
        ),
        "currentSituationNarrative": " ".join(
            safe_string_list(
                model_payload.get("clinicallyRelevantContext"),
                maximum=3,
            )
        ),
        "rootCauseNarrative": " ".join(
            safe_string_list(
                model_payload.get("contradictionsAndUncertainty"),
                maximum=3,
                allow_ecg_terms=True,
            )
        ),
        "importantLimitations": safe_string_list(
            model_payload.get("missingEvidence"),
            maximum=5,
        ),
    }


class SlmWidgetAssembler:
    def assemble(self, *, incident_id: str) -> dict[str, Any]:
        phase7_dir = _phase7_dir(incident_id)

        evidence = (
            _read_json(phase7_dir / "evidence_package.json")
            or {}
        )
        stored_response = _read_json(
            phase7_dir / "slm_response.json"
        )
        phase7_status = (
            _read_json(phase7_dir / "status.json")
            or {}
        )

        try:
            incident = incident_coordinator.get_incident(incident_id)
        except FileNotFoundError:
            incident = {}

        if not evidence:
            evidence = {"incident": incident}

        deterministic = _deterministic_narrative(evidence)
        model_payload, model_state = parse_model_payload(stored_response)
        narrative = _extract_widget_narrative(model_payload)

        episode_text = safe_text(
            narrative.get("episodeNarrative"),
            allow_ecg_terms=True,
        )
        arrhythmia_text = safe_text(
            narrative.get("arrhythmiaNarrative"),
            allow_ecg_terms=True,
        )
        morphology_text = safe_text(
            narrative.get("morphologyNarrative"),
            allow_ecg_terms=True,
        )
        current_text = safe_text(
            narrative.get("currentSituationNarrative")
            or _dict(narrative.get("currentSituation")).get("narrative")
        )
        root_cause_text = safe_text(
            narrative.get("rootCauseNarrative"),
            allow_ecg_terms=True,
        )

        (
            incident_view,
            ecg,
            _reference,
            temporal,
            _current_state,
        ) = _evidence_views(evidence)

        confidence = (
            _dict(ecg.get("deterministicConfidence"))
            or _dict(ecg.get("confidence"))
        )
        temporal_counts = _dict(
            temporal.get("countsByTemporalBucket")
            or temporal.get("countsByBucket")
        )
        episode_near_administration_count = int(
            _number(
                temporal.get(
                    "episodeNearMedicationAdministrationCount"
                )
            )
            or 0
        )

        limitations = []
        limitations.extend(
            safe_string_list(
                evidence.get("limitations"),
                maximum=6,
                allow_ecg_terms=True,
            )
        )
        limitations.extend(
            safe_string_list(
                narrative.get("importantLimitations"),
                maximum=4,
                allow_ecg_terms=True,
            )
        )
        limitations = list(dict.fromkeys(limitations))[:8]

        analysis_status = (
            evidence.get("analysisStatus")
            or incident_view.get("analysisStatus")
            or incident.get("analysisStatus")
            or "pending"
        )
        severity = (
            incident_view.get("severity")
            or incident.get("severity")
            or "warning"
        )

        contributors = validated_contributors(
            model_payload,
            episode_near_medication_administration_count=(
                episode_near_administration_count
            ),
            maximum=2,
        )

        widget = {
            "headline": (
                safe_text(
                    narrative.get("headline"),
                    allow_ecg_terms=True,
                )
                or deterministic["headline"]
            ),
            "statusCode": (
                "review_required"
                if analysis_status in {"ready", "partial", "complete"}
                else "processing"
            ),
            "statusLabel": (
                "Clinical review required"
                if analysis_status in {"ready", "partial", "complete"}
                else "Analysis in progress"
            ),
            "severity": severity,
            "episodeNarrative": (
                episode_text or deterministic["episodeNarrative"]
            ),
            "arrhythmiaNarrative": (
                arrhythmia_text
                or deterministic["arrhythmiaNarrative"]
            ),
            "morphologyNarrative": (
                morphology_text
                or deterministic["morphologyNarrative"]
            ),
            "currentSituation": {
                **deterministic["currentSituation"],
                "narrative": (
                    current_text
                    or deterministic["currentSituation"]["narrative"]
                ),
            },
            "rootCauseNarrative": (
                root_cause_text
                or deterministic["rootCauseNarrative"]
            ),
            "possibleContributors": contributors,
            "importantFindings": safe_string_list(
                narrative.get("importantFindings"),
                maximum=5,
                allow_ecg_terms=True,
            ),
            "importantLimitations": limitations,
            "recommendedNextChecks": RECOMMENDED_NEXT_CHECKS,
            "keyMetrics": _deterministic_metrics(evidence),
            "confidence": {
                "ecgEvidenceScore": _round_or_none(
                    confidence.get("score"),
                    1,
                ),
                "ecgEvidenceLabel": (
                    confidence.get("grade") or "unknown"
                ),
                "rootCauseConfidenceScore": max(
                    [
                        item["confidenceScore"]
                        for item in contributors
                    ],
                    default=0.0,
                ),
                "rootCauseConfidenceLabel": (
                    "low" if contributors else "insufficient"
                ),
            },
            "rootCauseConclusion": "No confirmed root cause",
            "displayDisclaimer": DISCLAIMER,
            "temporalSummary": {
                "countsByBucket": temporal_counts,
                "episodeNearMedicationAdministrationCount": (
                    episode_near_administration_count
                ),
            },
        }

        return {
            "schemaVersion": "slm-widget-v1",
            "incidentId": incident_id,
            "status": (
                phase7_status.get("state")
                or phase7_status.get("status")
                or (
                    "completed"
                    if stored_response
                    else "deterministic_fallback"
                )
            ),
            "widgetInterpretation": widget,
            "modelState": model_state,
            "provenance": {
                "deterministicOverlay": True,
                "rawWaveformsIncluded": False,
                "isIndependentDiagnosis": False,
            },
        }


slm_widget_assembler = SlmWidgetAssembler()
