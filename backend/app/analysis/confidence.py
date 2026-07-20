from __future__ import annotations

from typing import Any, Mapping
from copy import deepcopy
import numpy as np

from app.analysis.constants import (
    CONFIDENCE_GRADE_BOUNDS,
)


def _grade(
    score: float,
    essential_missing: bool,
) -> str:
    if essential_missing:
        return (
            "insufficient"
            if score < 55
            else "low"
        )

    for lower, grade in (
        CONFIDENCE_GRADE_BOUNDS
    ):
        if score >= lower:
            return grade

    return "insufficient"


def calculate_confidence(
    metadata: dict[str, Any],
    quality: dict[str, Any],
    r_peaks: dict[str, Any],
    rr: dict[str, Any],
    segmentation: dict[str, Any],
    qrs: dict[str, Any],
    morphology: dict[str, Any],
) -> dict[str, Any]:
    quality_score = float(
        quality.get(
            "overall",
            {},
        ).get(
            "score",
            0.0,
        )
    )

    usable_count = int(
        quality.get(
            "overall",
            {},
        ).get(
            "usableLeadCount",
            0,
        )
    )

    lead_score = min(
        100.0,
        usable_count
        / 6.0
        * 100.0,
    )

    r_score = float(
        r_peaks.get(
            "confidence",
            0.0,
        )
    )

    alignment_ms = r_peaks.get(
        (
            "triggerAlignmentError"
            "Milliseconds"
        )
    )

    alignment_score = (
        0.0
        if alignment_ms is None
        else max(
            0.0,
            100.0
            - abs(
                float(alignment_ms)
            )
            / 2.0,
        )
    )

    reference_count = len(
        segmentation.get(
            "selectedReferenceBeatIndices"
        )
        or []
    )

    reference_score = min(
        100.0,
        reference_count
        / 5.0
        * 100.0,
    )

    qrs_confidences = [
        value.get(
            "triggerBeat",
            {},
        ).get(
            "confidence",
            0.0,
        )
        for value
        in qrs.get(
            "leadResults",
            {},
        ).values()
        if value.get(
            "triggerBeat",
            {},
        ).get(
            "status"
        )
        == "ready"
    ]

    qrs_score = (
        float(
            np.median(
                qrs_confidences
            )
        )
        if qrs_confidences
        else 0.0
    )

    morphology_score = float(
        morphology.get(
            "morphologyConfidence",
            0.0,
        )
    )

    completeness = (
        metadata.get(
            "captureCompleteness"
        )
        or {}
    )

    if completeness.get(
        "captureComplete"
    ):
        capture_score = 100.0
    elif completeness:
        capture_score = 65.0
    else:
        capture_score = 50.0

    trigger_complete = bool(
        segmentation.get(
            "triggerBeat",
            {},
        ).get(
            "boundaryComplete"
        )
    )

    boundary_count = int(
        segmentation.get(
            "boundaryIncompleteBeatCount",
            0,
        )
    )

    if (
        trigger_complete
        and boundary_count == 0
    ):
        boundary_score = 100.0
    elif trigger_complete:
        boundary_score = 70.0
    else:
        boundary_score = 20.0

    components = {
        "signalQuality": (
            quality_score
        ),
        "usableLeadCount": (
            lead_score
        ),
        "rPeakAgreementAndDetection": (
            r_score
        ),
        "annotationToPeakAlignment": (
            alignment_score
        ),
        "referenceBeatCount": (
            reference_score
        ),
        "qrsConfidence": (
            qrs_score
        ),
        "morphologyAgreement": (
            morphology_score
        ),
        "captureCompleteness": (
            capture_score
        ),
        "boundaryCompleteness": (
            boundary_score
        ),
    }

    weights = {
        "signalQuality": 0.18,
        "usableLeadCount": 0.10,
        "rPeakAgreementAndDetection": (
            0.17
        ),
        "annotationToPeakAlignment": (
            0.08
        ),
        "referenceBeatCount": 0.12,
        "qrsConfidence": 0.12,
        "morphologyAgreement": 0.13,
        "captureCompleteness": 0.05,
        "boundaryCompleteness": 0.05,
    }

    score = sum(
        components[key]
        * weights[key]
        for key in components
    )

    failed_measurements = []

    for name, result in (
        (
            "rPeakAnalysis",
            r_peaks,
        ),
        (
            "rrAnalysis",
            rr,
        ),
        (
            "qrsAnalysis",
            qrs,
        ),
        (
            "morphology",
            morphology,
        ),
    ):
        if (
            result.get("status")
            == "failed"
        ):
            failed_measurements.append(
                name
            )

    essential_missing = bool(
        r_peaks.get(
            "detectedBeatCount",
            0,
        )
        < 2
        or not trigger_complete
        or not qrs_confidences
        or morphology.get("status")
        == "failed"
    )

    if essential_missing:
        score = min(
            score,
            49.0,
        )

    positive = [
        key
        for key, value
        in components.items()
        if value >= 75.0
    ]

    negative = [
        key
        for key, value
        in components.items()
        if value < 50.0
    ]

    limitations = list(
        quality.get(
            "limitations"
        )
        or []
    )

    if reference_count < 3:
        limitations.append(
            (
                "Fewer than three "
                "reference beats were "
                "available."
            )
        )

    if not trigger_complete:
        limitations.append(
            (
                "The trigger beat window "
                "is incomplete at a "
                "capture boundary."
            )
        )

    if failed_measurements:
        limitations.append(
            (
                "Failed deterministic "
                "measurements: "
                f"{', '.join(failed_measurements)}."
            )
        )

    return {
        "score": round(
            score,
            2,
        ),
        "grade": _grade(
            score,
            essential_missing,
        ),
        "positiveReasons": positive,
        "negativeReasons": negative,
        "components": {
            key: round(value, 2)
            for key, value
            in components.items()
        },
        "limitations": limitations,
        "excludedEvidence": (
            failed_measurements
        ),
        "essentialMeasurementsMissing": (
            essential_missing
        ),
    }
    



def apply_physiology_confidence_penalties(
    existing_confidence: Mapping[str, Any],
    *,
    r_peak_analysis: Mapping[str, Any],
    rr_analysis: Mapping[str, Any],
    qrs_analysis: Mapping[str, Any],
    lead_agreement: Mapping[str, Any],
) -> dict[str, Any]:
    result = deepcopy(dict(existing_confidence))
    pre_penalty_score = float(result.get("score") or 0.0)
    score = pre_penalty_score
    negative_reasons = list(result.get("negativeReasons") or [])
    limitations = list(result.get("limitations") or [])
    excluded_evidence = list(result.get("excludedEvidence") or [])
    positive_reasons = list(result.get("positiveReasons") or [])
    penalties: list[dict[str, Any]] = []
    essential_missing = bool(result.get("essentialMeasurementsMissing"))

    def penalize(reason: str, points: float, limitation: str, evidence: str | None = None) -> None:
        nonlocal score
        if reason in negative_reasons:
            return
        score -= points
        negative_reasons.append(reason)
        limitations.append(limitation)
        penalties.append({"reason": reason, "points": points})
        if evidence:
            excluded_evidence.append(evidence)

    timing_status = str(r_peak_analysis.get("status") or "failed")
    rr_status = str(rr_analysis.get("status") or "failed")
    validation = r_peak_analysis.get("validation") or {}
    maximum_hr = rr_analysis.get("maximumHeartRateBpm")
    minimum_rr = rr_analysis.get("minimumRrMilliseconds")
    excluded_percent = float(rr_analysis.get("excludedIntervalPercent") or 0.0)

    if timing_status == "failed" or rr_status == "failed":
        penalize(
            "essentialTimingMeasurementFailed",
            45.0,
            "Reliable R-peak/RR timing was not available.",
            "rhythmAndTimingEvidence",
        )
        essential_missing = True
    elif timing_status == "partial" or rr_status == "partial":
        penalize(
            "timingValidationPartial",
            18.0,
            "R-peak or RR validation was partial; rhythm-dependent conclusions are confidence-limited.",
            "highConfidenceRhythmEvidence",
        )
        score = min(score, 79.0)

    if maximum_hr is not None and float(maximum_hr) > 240.0:
        penalize(
            "implausibleHeartRate",
            30.0,
            "At least one calculated instantaneous heart rate exceeded the supported validation range.",
            "maximumHeartRateBpm",
        )
        essential_missing = True
        score = min(score, 49.0)

    if minimum_rr is not None and float(minimum_rr) < 250.0:
        penalize(
            "implausibleRrIntervals",
            30.0,
            "At least one RR interval remained below the supported minimum after validation.",
            "minimumRrMilliseconds",
        )
        essential_missing = True
        score = min(score, 49.0)

    if excluded_percent > 25.0:
        penalize(
            "manyExcludedRrIntervals",
            25.0,
            "More than 25% of RR intervals were excluded as physiologically unsupported.",
            "rrDerivedBurdenAndPatternEvidence",
        )
        score = min(score, 59.0)
    elif excluded_percent > 10.0:
        penalize(
            "excludedRrIntervals",
            15.0,
            "More than 10% of RR intervals were excluded as physiologically unsupported.",
        )
        score = min(score, 69.0)

    metadata_difference = validation.get("metadataHeartRateDifferenceFraction")
    if metadata_difference is not None and float(metadata_difference) > 0.60:
        penalize(
            "severeMetadataHeartRateMismatch",
            25.0,
            "Calculated median heart rate differs by more than 60% from stored trigger-heart-rate metadata.",
            "highConfidenceHeartRateInterpretation",
        )
        score = min(score, 59.0)
    elif metadata_difference is not None and float(metadata_difference) > 0.35:
        penalize(
            "metadataHeartRateMismatch",
            15.0,
            "Calculated median heart rate differs materially from stored trigger-heart-rate metadata.",
        )
        score = min(score, 69.0)

    overall_lead_agreement = float(lead_agreement.get("overallMultiLeadAgreementScore") or 0.0)
    if overall_lead_agreement < 55.0:
        penalize(
            "lowMultiLeadAgreement",
            20.0,
            "Overall multi-lead agreement is low.",
        )
    elif overall_lead_agreement < 70.0:
        penalize(
            "moderateMultiLeadAgreement",
            10.0,
            "Overall multi-lead agreement is only moderate.",
        )

    qrs_agreement = float(qrs_analysis.get("interLeadDurationAgreement") or 0.0)
    qrs_confidence = float(qrs_analysis.get("measurementConfidence") or 0.0)
    result.setdefault("components", {})["qrsConfidence"] = round(qrs_confidence, 2)
    if qrs_agreement < 0.30:
        penalize(
            "veryLowQrsDurationAgreement",
            25.0,
            "QRS duration has very low inter-lead agreement.",
            "highConfidenceQrsWidthConclusion",
        )
        score = min(score, 59.0)
    elif qrs_agreement < 0.50:
        penalize(
            "lowQrsDurationAgreement",
            15.0,
            "QRS duration has low inter-lead agreement.",
        )
        score = min(score, 69.0)

    premature = bool(rr_analysis.get("prematureTimingEvidence"))
    compensatory = rr_analysis.get("compensatoryPauseStatus")
    if not premature and compensatory in {"full", "incomplete"}:
        penalize(
            "inconsistentTimingEvidence",
            20.0,
            "Compensatory-pause classification conflicts with the absence of premature timing evidence.",
            "compensatoryPauseEvidence",
        )

    if timing_status != "ready" and "rPeakAgreementAndDetection" in positive_reasons:
        positive_reasons.remove("rPeakAgreementAndDetection")
    if qrs_confidence < 60.0 and "qrsConfidence" in positive_reasons:
        positive_reasons.remove("qrsConfidence")

    score = max(0.0, min(100.0, score))
    result.update(
        {
            "prePenaltyScore": round(pre_penalty_score, 2),
            "score": round(score, 2),
            "grade": _grade(score, essential_missing),
            "positiveReasons": positive_reasons,
            "negativeReasons": list(dict.fromkeys(negative_reasons)),
            "penalties": penalties,
            "limitations": list(dict.fromkeys(limitations)),
            "excludedEvidence": list(dict.fromkeys(excluded_evidence)),
            "essentialMeasurementsMissing": essential_missing,
        }
    )
    return result