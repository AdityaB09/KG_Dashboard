from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from app.phase7.config import (
    phase7_settings,
)


_RAW_KEYS = {
    "raw_mv",
    "centered_mv",
    "waveforms",
    "waveformarrays",
    "filteredsignals",
    "filteredsamples",
    "beatarrays",
    "signals",
}


def _mapping(
    value: Any,
) -> dict[str, Any]:
    return (
        dict(value)
        if isinstance(
            value,
            Mapping,
        )
        else {}
    )


def _list(
    value: Any,
) -> list[Any]:
    return (
        list(value)
        if isinstance(
            value,
            Sequence,
        )
        and not isinstance(
            value,
            (
                str,
                bytes,
                bytearray,
            ),
        )
        else []
    )


def _bounded(
    value: Any,
    maximum: int,
) -> list[Any]:
    return _list(value)[
        :maximum
    ]


def _number(
    value: Any,
) -> float | None:
    try:
        result = float(value)
    except (
        TypeError,
        ValueError,
    ):
        return None

    if result != result:
        return None

    if result in {
        float("inf"),
        float("-inf"),
    }:
        return None

    return result


def temporal_bucket(
    minutes_from_anchor: Any,
) -> str:
    minutes = _number(
        minutes_from_anchor
    )

    if minutes is None:
        return "time_unknown"

    absolute = abs(minutes)

    if absolute <= 15:
        return "episode_near"

    if absolute <= 60:
        return "within_one_hour"

    if absolute <= 24 * 60:
        return "within_one_day"

    if absolute <= 7 * 24 * 60:
        return "within_one_week"

    if absolute <= 90 * 24 * 60:
        return "historical"

    return "historical_remote"


def _compact_point(
    point: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        key: point.get(key)
        for key in (
            "resourceId",
            "value",
            "unit",
            "observedAt",
            "status",
            "minutesFromAnchor",
            "relation",
            "relationLabel",
        )
        if point.get(key)
        is not None
    } | {
        "temporalBucket": (
            temporal_bucket(
                point.get(
                    "minutesFromAnchor"
                )
            )
        )
    }


def _compact_trend(
    trend: Mapping[str, Any],
) -> dict[str, Any]:
    points = [
        _compact_point(
            _mapping(point)
        )
        for point in _bounded(
            trend.get("points"),
            4,
        )
    ]

    latest_minutes = None

    if points:
        latest_minutes = points[
            -1
        ].get(
            "minutesFromAnchor"
        )

    return {
        "field": trend.get("field"),
        "label": trend.get("label"),
        "latestValue": trend.get(
            "latestValue"
        ),
        "unit": trend.get("unit"),
        "latestAt": trend.get(
            "latestAt"
        ),
        "latestRelation": trend.get(
            "latestRelation"
        ),
        "latestRelationLabel": (
            trend.get(
                "latestRelationLabel"
            )
        ),
        "trendDirection": trend.get(
            "trendDirection"
        ),
        "classification": trend.get(
            "color"
        ),
        "temporalBucket": (
            temporal_bucket(
                latest_minutes
            )
        ),
        "points": points,
    }


def _compact_medication(
    item: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "id",
            "name",
            "resourceType",
            "status",
            "evidenceLevel",
            "doseValue",
            "doseUnit",
            "doseDisplay",
            "route",
            "instructions",
            "eventTime",
            "minutesFromAnchor",
            "relation",
            "relationLabel",
        )
        if item.get(key)
        is not None
    } | {
        "temporalBucket": (
            temporal_bucket(
                item.get(
                    "minutesFromAnchor"
                )
            )
        )
    }


def _compact_episode(
    item: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "episodeId": item.get(
            "episodeId"
        ),
        "status": item.get(
            "status"
        ),
        "signalQuality": (
            _mapping(
                item.get(
                    "signalQuality"
                )
            ).get("overall")
            or _mapping(
                item.get(
                    "signalQuality"
                )
            )
        ),
        "rPeakSummary": _mapping(
            item.get(
                "rPeakSummary"
            )
            or item.get(
                "rPeakAnalysis"
            )
        ),
        "rrSummary": _mapping(
            item.get("rrSummary")
        ),
        "heartRateSummary": _mapping(
            item.get(
                "heartRateSummary"
            )
        ),
        "qrsSummary": _mapping(
            item.get("qrsSummary")
        ),
        "morphologySummary": (
            _mapping(
                item.get(
                    "morphologySummary"
                )
            )
        ),
        "ventricularEctopyEvidence": (
            _mapping(
                item.get(
                    "ventricularEctopyEvidence"
                )
            )
        ),
        "leadAgreement": _mapping(
            item.get(
                "leadAgreement"
            )
        ),
        "ectopicBurden": _mapping(
            item.get(
                "ectopicBurden"
            )
        ),
        "confidence": _mapping(
            item.get(
                "confidence"
            )
        ),
        "limitations": _bounded(
            item.get(
                "limitations"
            ),
            20,
        ),
        "provenance": _mapping(
            item.get(
                "provenance"
            )
        ),
    }


def _deep_contains_raw_key(
    value: Any,
) -> bool:
    if isinstance(
        value,
        Mapping,
    ):
        for key, child in value.items():
            normalized = str(
                key
            ).replace(
                "_",
                "",
            ).lower()

            if normalized in {
                item.replace(
                    "_",
                    "",
                )
                for item in _RAW_KEYS
            }:
                return True

            if _deep_contains_raw_key(
                child
            ):
                return True

    elif isinstance(
        value,
        Sequence,
    ) and not isinstance(
        value,
        (
            str,
            bytes,
            bytearray,
        ),
    ):
        return any(
            _deep_contains_raw_key(
                item
            )
            for item in value
        )

    return False


def build_evidence_package(
    *,
    incident: Mapping[str, Any],
    incident_analysis: Mapping[
        str,
        Any,
    ],
    slm_context: Mapping[str, Any],
    clinical_context: Mapping[
        str,
        Any,
    ],
    context_resolution: Mapping[
        str,
        Any,
    ],
    schema_version: str,
) -> dict[str, Any]:
    incident_analysis_data = (
        _mapping(
            incident_analysis
        )
    )

    deterministic = _mapping(
        slm_context.get(
            "deterministicEcgEvidence"
        )
    )

    episode_analyses = [
        _compact_episode(
            _mapping(item)
        )
        for item in _bounded(
            deterministic.get(
                "episodeAnalyses"
            ),
            phase7_settings
            .maximum_episode_summaries,
        )
    ]

    incident_summary = (
        _mapping(
            deterministic.get(
                "incidentAnalysis"
            )
        )
        or incident_analysis_data
    )

    clinical_status = str(
        clinical_context.get(
            "status"
        )
        or slm_context.get(
            "contextStatus"
        )
        or "not_loaded"
    )

    prompt_mode = (
        "ECG_PLUS_FHIR"
        if clinical_status
        in {
            "ready",
            "partial",
        }
        else "ECG_ONLY"
    )

    lab_trends = [
        _compact_trend(
            _mapping(item)
        )
        for item in _bounded(
            clinical_context.get(
                "labTrends"
            ),
            phase7_settings
            .maximum_lab_trends,
        )
    ]

    vital_trends = [
        _compact_trend(
            _mapping(item)
        )
        for item in _bounded(
            clinical_context.get(
                "vitalTrends"
            ),
            phase7_settings
            .maximum_vital_trends,
        )
    ]

    medications = [
        _compact_medication(
            _mapping(item)
        )
        for item in _bounded(
            clinical_context.get(
                "medicationTimeline"
            ),
            phase7_settings
            .maximum_medications,
        )
    ]

    burden = _mapping(
        incident_summary.get(
            "ectopicBurden"
        )
    )

    reference_count = (
        burden.get(
            "referenceVAnnotationCount"
        )
        or burden.get(
            "uniqueTriggerCount"
        )
    )

    candidate_count = burden.get(
        "uniqueAbnormalMorphologyCandidateCount"
    )

    contradictions: list[
        dict[str, Any]
    ] = []

    if (
        reference_count is not None
        and candidate_count == 0
    ):
        contradictions.append(
            {
                "type": (
                    "different_measurement_definitions"
                ),
                "referenceEvidence": {
                    "source": (
                        "PhysioNet INCART atr"
                    ),
                    "referenceVAnnotationCount": (
                        reference_count
                    ),
                },
                "independentEvidence": {
                    "abnormalMorphologyCandidateCount": (
                        candidate_count
                    ),
                },
                "interpretationRule": (
                    "A zero independent abnormal-"
                    "morphology-candidate count does "
                    "not negate dataset V reference "
                    "annotations. These measure "
                    "different concepts."
                ),
            }
        )

    if str(
        incident_analysis_data.get(
            "status"
        )
    ) == "partial":
        contradictions.append(
            {
                "type": (
                    "measurement_uncertainty"
                ),
                "detail": (
                    "Incident analysis is partial. "
                    "Use section-level confidence "
                    "and limitations."
                ),
            }
        )

    missing = list(
        dict.fromkeys(
            str(item)
            for item in _bounded(
                slm_context.get(
                    "missingSignals"
                ),
                50,
            )
            if item
        )
    )

    if prompt_mode == "ECG_ONLY":
        missing.extend(
            [
                "patientSummary",
                "labTrends",
                "vitalTrends",
                "medicationTimeline",
                "conditions",
                "encounters",
                "diagnosticReports",
                "documents",
            ]
        )

    missing = list(
        dict.fromkeys(missing)
    )

    limitations = list(
        dict.fromkeys(
            [
                *(
                    _bounded(
                        slm_context.get(
                            "limitations"
                        ),
                        40,
                    )
                ),
                *(
                    _bounded(
                        incident_analysis_data.get(
                            "limitations"
                        ),
                        40,
                    )
                ),
                *(
                    _bounded(
                        clinical_context.get(
                            "limitations"
                        ),
                        40,
                    )
                ),
            ]
        )
    )

    credential_limitation = (
        context_resolution.get(
            "limitation"
        )
    )

    if credential_limitation:
        limitations.append(
            credential_limitation
        )

    package = {
        "schemaVersion": (
            schema_version
        ),
        "incidentId": incident.get(
            "id"
        )
        or incident_analysis_data.get(
            "incidentId"
        ),
        "promptMode": prompt_mode,
        "analysisStatus": (
            incident_analysis_data.get(
                "status"
            )
        ),
        "contextStatus": (
            clinical_status
        ),
        "incident": {
            "display": incident.get(
                "display"
            )
            or _mapping(
                slm_context.get(
                    "episodeAnnotation"
                )
            ).get("display"),
            "category": incident.get(
                "primaryCategory"
            )
            or _mapping(
                slm_context.get(
                    "episodeAnnotation"
                )
            ).get("category"),
            "severity": incident.get(
                "severity"
            )
            or _mapping(
                slm_context.get(
                    "episodeAnnotation"
                )
            ).get("severity"),
            "incidentStartSeconds": (
                incident.get(
                    "incidentStartSeconds"
                )
            ),
            "incidentEndSeconds": (
                incident.get(
                    "incidentEndSeconds"
                )
            ),
            "durationSeconds": (
                incident.get(
                    "durationSeconds"
                )
                or _mapping(
                    slm_context.get(
                        "episodeAnnotation"
                    )
                ).get(
                    "durationSeconds"
                )
            ),
            "episodeCount": len(
                incident.get(
                    "episodeIds"
                )
                or episode_analyses
            ),
            "primaryEpisodeId": (
                incident.get(
                    "primaryEpisodeId"
                )
            ),
            "bestContextEpisodeId": (
                incident.get(
                    "bestContextEpisodeId"
                )
            ),
            "captureCompleteness": (
                _mapping(
                    incident.get(
                        "captureCompleteness"
                    )
                )
            ),
        },
        "evidence": {
            "independentlyMeasuredEcg": {
                "signalQuality": (
                    _mapping(
                        incident_summary.get(
                            "signalQuality"
                        )
                    )
                ),
                "rhythm": _mapping(
                    incident_summary.get(
                        "rhythm"
                    )
                ),
                "qrs": _mapping(
                    incident_summary.get(
                        "qrs"
                    )
                ),
                "morphology": _mapping(
                    incident_summary.get(
                        "morphology"
                    )
                ),
                "leadAgreement": (
                    _mapping(
                        incident_summary.get(
                            "leadAgreement"
                        )
                    )
                ),
                "crossEpisodeAgreement": (
                    _mapping(
                        incident_summary.get(
                            "crossEpisodeAgreement"
                        )
                    )
                ),
                "confidence": _mapping(
                    incident_summary.get(
                        "confidence"
                    )
                ),
                "independentCandidateDetection": {
                    "abnormalMorphologyCandidateCount": (
                        candidate_count
                    ),
                    "candidatePercent": burden.get(
                        "incidentEctopicBurdenPercent"
                    ),
                    "doesNotNegateReferenceAnnotations": (
                        True
                    ),
                },
                "episodeSummaries": (
                    episode_analyses
                ),
            },
            "datasetReference": {
                "source": (
                    _mapping(
                        slm_context.get(
                            "episodeAnnotation"
                        )
                    ).get(
                        "sourceName"
                    )
                    or "PhysioNet INCART"
                ),
                "sourceType": (
                    "dataset_reference_annotation"
                ),
                "triggerCounts": (
                    incident.get(
                        "triggerCounts"
                    )
                    or _mapping(
                        slm_context.get(
                            "episodeAnnotation"
                        )
                    ).get(
                        "triggerCounts"
                    )
                ),
                "uniqueReferenceTriggerCount": (
                    reference_count
                ),
                "isIndependentDiagnosis": (
                    False
                ),
            },
            "clinicalContext": {
                "status": clinical_status,
                "contextAnchor": (
                    _mapping(
                        clinical_context.get(
                            "contextAnchor"
                        )
                    )
                ),
                "patientSummary": (
                    _mapping(
                        clinical_context.get(
                            "patientSummary"
                        )
                    )
                ),
                "labTrends": lab_trends,
                "vitalTrends": (
                    vital_trends
                ),
                "medicationTimeline": (
                    medications
                ),
                "conditions": _bounded(
                    clinical_context.get(
                        "conditions"
                    ),
                    phase7_settings
                    .maximum_conditions,
                ),
                "encounters": _bounded(
                    clinical_context.get(
                        "encounters"
                    ),
                    phase7_settings
                    .maximum_encounters,
                ),
                "diagnosticReports": (
                    _bounded(
                        clinical_context.get(
                            "diagnosticReports"
                        ),
                        phase7_settings
                        .maximum_reports,
                    )
                ),
                "documents": _bounded(
                    clinical_context.get(
                        "documents"
                    )
                    or clinical_context.get(
                        "documentMetadata"
                    ),
                    phase7_settings
                    .maximum_documents,
                ),
                "dataQuality": _mapping(
                    clinical_context.get(
                        "dataQuality"
                    )
                    or clinical_context.get(
                        "clinicalDataQuality"
                    )
                ),
                "sourceResolution": (
                    dict(
                        context_resolution
                    )
                ),
            },
        },
        "contradictionsAndDistinctions": (
            contradictions
        ),
        "missingEvidence": missing,
        "limitations": list(
            dict.fromkeys(
                limitations
            )
        ),
        "provenance": {
            "waveformSource": (
                _mapping(
                    slm_context.get(
                        "provenance"
                    )
                ).get(
                    "waveformSource"
                )
            ),
            "triggerSource": (
                _mapping(
                    slm_context.get(
                        "provenance"
                    )
                ).get(
                    "triggerSource"
                )
            ),
            "clinicalContextSource": (
                _mapping(
                    clinical_context.get(
                        "provenance"
                    )
                ).get("source")
                or _mapping(
                    slm_context.get(
                        "provenance"
                    )
                ).get(
                    "clinicalContextSource"
                )
            ),
            "deterministicAnalysis": (
                _mapping(
                    incident_analysis_data.get(
                        "provenance"
                    )
                )
            ),
        },
        "safety": {
            "mode": "research",
            "isIndependentDiagnosis": (
                False
            ),
            "rawWaveformsIncluded": (
                False
            ),
            "samePatientVerified": (
                False
                if str(
                    incident.get("mode")
                    or "research"
                )
                == "research"
                else None
            ),
            "requiredOutputBehavior": [
                (
                    "Distinguish deterministic "
                    "measurements from dataset "
                    "reference annotations."
                ),
                (
                    "Distinguish episode-near "
                    "clinical data from historical "
                    "or remote clinical data."
                ),
                (
                    "Do not infer causation from "
                    "the controlled INCART and "
                    "Oracle research pairing."
                ),
                (
                    "Do not provide an independent "
                    "diagnosis."
                ),
            ],
        },
    }

    package["validation"] = {
        "rawWaveformKeyDetected": (
            _deep_contains_raw_key(
                package
            )
        ),
        "isIndependentDiagnosis": (
            False
        ),
    }

    return package
