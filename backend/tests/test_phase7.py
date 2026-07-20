from __future__ import annotations

from app.phase7.evidence import (
    build_evidence_package,
    temporal_bucket,
)
from app.phase7.prompt_builder import (
    build_prompt_package,
)


def sample_inputs():
    incident = {
        "id": "inc-test",
        "mode": "research",
        "display": (
            "Ventricular Ectopy incident"
        ),
        "primaryCategory": (
            "ventricular_ectopy"
        ),
        "severity": "warning",
        "episodeIds": [
            "ep-1",
        ],
        "primaryEpisodeId": "ep-1",
        "bestContextEpisodeId": (
            "ep-1"
        ),
    }

    analysis = {
        "incidentId": "inc-test",
        "status": "partial",
        "rhythm": {
            "medianHeartRateBpm": (
                89.22
            ),
        },
        "qrs": {
            "medianTriggerQrsDurationMilliseconds": (
                100
            ),
        },
        "morphology": {
            "medianDifferenceScore": (
                0.29
            ),
        },
        "ectopicBurden": {
            "referenceVAnnotationCount": (
                58
            ),
            "uniqueAbnormalMorphologyCandidateCount": (
                0
            ),
            "incidentEctopicBurdenPercent": (
                0
            ),
        },
        "confidence": {
            "score": 82.37,
            "grade": "high",
        },
        "limitations": [
            "QRS evidence is partial.",
        ],
        "provenance": {
            "isIndependentDiagnosis": (
                False
            ),
        },
    }

    slm_context = {
        "contextStatus": "ready",
        "episodeAnnotation": {
            "sourceName": (
                "PhysioNet INCART"
            ),
            "triggerCounts": {
                "V": 58,
            },
        },
        "missingSignals": [
            "ppg",
        ],
        "deterministicEcgEvidence": {
            "episodeAnalyses": [
                {
                    "episodeId": "ep-1",
                    "status": "ready",
                    "heartRateSummary": {
                        "medianHeartRateBpm": (
                            89.2
                        ),
                    },
                    "limitations": [],
                    "provenance": {
                        "isIndependentDiagnosis": (
                            False
                        ),
                    },
                }
            ],
            "incidentAnalysis": (
                analysis
            ),
        },
        "limitations": [],
        "provenance": {
            "waveformSource": (
                "PhysioNet INCART"
            ),
            "triggerSource": (
                "INCART atr"
            ),
        },
    }

    context = {
        "status": "ready",
        "contextAnchor": {
            "value": (
                "2026-07-16T00:00:00Z"
            ),
        },
        "patientSummary": {
            "id": "test-patient",
        },
        "labTrends": [
            {
                "field": "potassium",
                "latestValue": 5.8,
                "unit": "mmol/L",
                "points": [
                    {
                        "value": 5.8,
                        "unit": "mmol/L",
                        "minutesFromAnchor": (
                            -1000000
                        ),
                    }
                ],
            }
        ],
        "vitalTrends": [],
        "medicationTimeline": [],
        "conditions": [],
        "encounters": [],
        "diagnosticReports": [],
        "documents": [],
        "provenance": {
            "source": "Oracle FHIR",
        },
    }

    return (
        incident,
        analysis,
        slm_context,
        context,
    )


def test_temporal_buckets():
    assert temporal_bucket(
        5
    ) == "episode_near"

    assert temporal_bucket(
        45
    ) == "within_one_hour"

    assert temporal_bucket(
        1000000
    ) == "historical_remote"


def test_evidence_separates_reference_and_candidates():
    (
        incident,
        analysis,
        slm_context,
        context,
    ) = sample_inputs()

    evidence = build_evidence_package(
        incident=incident,
        incident_analysis=(
            analysis
        ),
        slm_context=slm_context,
        clinical_context=context,
        context_resolution={
            "source": "test",
        },
        schema_version="phase-7-v1",
    )

    dataset = evidence[
        "evidence"
    ][
        "datasetReference"
    ]

    independent = evidence[
        "evidence"
    ][
        "independentlyMeasuredEcg"
    ][
        "independentCandidateDetection"
    ]

    assert (
        dataset[
            "uniqueReferenceTriggerCount"
        ]
        == 58
    )

    assert (
        independent[
            "abnormalMorphologyCandidateCount"
        ]
        == 0
    )

    assert independent[
        "doesNotNegateReferenceAnnotations"
    ] is True


def test_prompt_contains_no_raw_waveform_arrays():
    (
        incident,
        analysis,
        slm_context,
        context,
    ) = sample_inputs()

    evidence = build_evidence_package(
        incident=incident,
        incident_analysis=(
            analysis
        ),
        slm_context=slm_context,
        clinical_context=context,
        context_resolution={
            "source": "test",
        },
        schema_version="phase-7-v1",
    )

    prompt = build_prompt_package(
        evidence,
        schema_version=(
            "phase-7-v1"
        ),
    )

    assert (
        prompt[
            "validation"
        ][
            "status"
        ]
        == "ready"
    )

    assert (
        "centered_mv"
        not in prompt[
            "promptText"
        ]
    )

    assert (
        "raw_mv"
        not in prompt[
            "promptText"
        ]
    )


def test_ecg_only_when_context_is_unavailable():
    (
        incident,
        analysis,
        slm_context,
        context,
    ) = sample_inputs()

    context["status"] = (
        "unavailable"
    )

    evidence = build_evidence_package(
        incident=incident,
        incident_analysis=(
            analysis
        ),
        slm_context=slm_context,
        clinical_context=context,
        context_resolution={
            "source": "none",
        },
        schema_version="phase-7-v1",
    )

    assert evidence[
        "promptMode"
    ] == "ECG_ONLY"
