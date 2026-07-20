from __future__ import annotations

from typing import Any

import numpy as np

from app.analysis.constants import (
    MORPHOLOGY_ALIGNMENT_MS,
    MORPHOLOGY_GRADE_BOUNDS,
)
from app.analysis.qrs import (
    measure_qrs,
)


def _grade(score: float) -> str:
    for upper, grade in (
        MORPHOLOGY_GRADE_BOUNDS
    ):
        if score <= upper:
            return grade

    return "markedly_different"


def _align(
    reference: np.ndarray,
    target: np.ndarray,
    max_shift: int,
) -> tuple[np.ndarray, int]:
    best = target
    best_shift = 0
    best_correlation = -2.0

    for shift in range(
        -max_shift,
        max_shift + 1,
    ):
        shifted = np.roll(
            target,
            shift,
        )

        if shift > 0:
            shifted[:shift] = (
                shifted[shift]
            )
        elif shift < 0:
            shifted[shift:] = (
                shifted[shift - 1]
            )

        if (
            np.std(reference) < 1e-12
            or np.std(shifted) < 1e-12
        ):
            correlation = -1.0
        else:
            correlation = float(
                np.corrcoef(
                    reference,
                    shifted,
                )[0, 1]
            )

        if (
            correlation
            > best_correlation
        ):
            best_correlation = (
                correlation
            )

            best = shifted
            best_shift = shift

    return best, best_shift


def compare_templates(
    trigger: np.ndarray | None,
    baseline: np.ndarray | None,
    sampling_rate_hz: float,
) -> dict[str, Any]:
    if (
        trigger is None
        or baseline is None
        or trigger.size
        != baseline.size
        or trigger.size < 8
    ):
        return {
            "status": "failed",
            "failureReason": (
                "insufficient_reference"
            ),
            "confidence": 0.0,
        }

    trigger = np.asarray(
        trigger,
        dtype=np.float64,
    )

    baseline = np.asarray(
        baseline,
        dtype=np.float64,
    )

    max_shift = max(
        1,
        int(
            round(
                MORPHOLOGY_ALIGNMENT_MS
                * sampling_rate_hz
                / 1000.0
            )
        ),
    )

    (
        aligned,
        shift,
    ) = _align(
        baseline,
        trigger,
        max_shift,
    )

    baseline_centered = (
        baseline
        - np.median(baseline)
    )

    trigger_centered = (
        aligned
        - np.median(aligned)
    )

    baseline_norm = (
        np.linalg.norm(
            baseline_centered
        )
    )

    trigger_norm = (
        np.linalg.norm(
            trigger_centered
        )
    )

    if (
        baseline_norm < 1e-9
        or trigger_norm < 1e-9
    ):
        return {
            "status": "failed",
            "failureReason": (
                "near_flat_template"
            ),
            "confidence": 0.0,
        }

    normalized_baseline = (
        baseline_centered
        / baseline_norm
    )

    normalized_trigger = (
        trigger_centered
        / trigger_norm
    )

    pearson = float(
        np.corrcoef(
            normalized_baseline,
            normalized_trigger,
        )[0, 1]
    )

    cosine = float(
        np.dot(
            normalized_baseline,
            normalized_trigger,
        )
    )

    euclidean = float(
        np.linalg.norm(
            normalized_baseline
            - normalized_trigger
        )
        / np.sqrt(
            trigger.size
        )
    )

    baseline_qrs = measure_qrs(
        baseline,
        sampling_rate_hz,
    )

    trigger_qrs = measure_qrs(
        aligned,
        sampling_rate_hz,
    )

    width_difference = None
    area_difference = None
    polarity_difference = None
    slope_difference = None

    if (
        baseline_qrs.get("status")
        == "ready"
        and trigger_qrs.get("status")
        == "ready"
    ):
        width_difference = (
            trigger_qrs[
                "qrsDurationMilliseconds"
            ]
            - baseline_qrs[
                "qrsDurationMilliseconds"
            ]
        )

        area_difference = (
            trigger_qrs[
                "qrsAreaMvSeconds"
            ]
            - baseline_qrs[
                "qrsAreaMvSeconds"
            ]
        )

        polarity_difference = (
            trigger_qrs[
                "qrsPolarity"
            ]
            != baseline_qrs[
                "qrsPolarity"
            ]
        )

        baseline_slope = max(
            abs(
                baseline_qrs[
                    "maximumUpstrokeMvPerSecond"
                ]
            ),
            abs(
                baseline_qrs[
                    "maximumDownstrokeMvPerSecond"
                ]
            ),
        )

        trigger_slope = max(
            abs(
                trigger_qrs[
                    "maximumUpstrokeMvPerSecond"
                ]
            ),
            abs(
                trigger_qrs[
                    "maximumDownstrokeMvPerSecond"
                ]
            ),
        )

        slope_difference = (
            trigger_slope
            - baseline_slope
        )

    amplitude_ratio = float(
        (
            np.percentile(
                np.abs(
                    trigger_centered
                ),
                98,
            )
            + 1e-9
        )
        / (
            np.percentile(
                np.abs(
                    baseline_centered
                ),
                98,
            )
            + 1e-9
        )
    )

    difference_score = float(
        np.clip(
            (
                0.35
                * (
                    1.0
                    - max(
                        -1.0,
                        min(
                            1.0,
                            pearson,
                        ),
                    )
                )
                / 2.0
                + 0.25
                * (
                    1.0
                    - max(
                        -1.0,
                        min(
                            1.0,
                            cosine,
                        ),
                    )
                )
                / 2.0
                + 0.20
                * min(
                    1.0,
                    euclidean * 4.0,
                )
                + 0.10
                * min(
                    1.0,
                    abs(
                        np.log(
                            max(
                                amplitude_ratio,
                                1e-6,
                            )
                        )
                    )
                    / np.log(3.0),
                )
                + 0.10
                * (
                    1.0
                    if polarity_difference
                    else 0.0
                )
            ),
            0.0,
            1.0,
        )
    )

    confidence = min(
        100.0,
        (
            70.0
            + 15.0
            * min(
                1.0,
                baseline_norm,
            )
            + 15.0
            * min(
                1.0,
                trigger_norm,
            )
        ),
    )

    return {
        "status": "ready",
        "alignmentShiftSamples": (
            shift
        ),
        "alignmentShiftMilliseconds": (
            round(
                1000.0
                * shift
                / sampling_rate_hz,
                3,
            )
        ),
        "amplitudeNormalizationApplied": (
            True
        ),
        "pearsonCorrelation": round(
            pearson,
            6,
        ),
        "cosineSimilarity": round(
            cosine,
            6,
        ),
        "normalizedEuclideanDistance": (
            round(
                euclidean,
                6,
            )
        ),
        "widthDifferenceMilliseconds": (
            round(
                width_difference,
                3,
            )
            if width_difference
            is not None
            else None
        ),
        "amplitudeRatio": round(
            amplitude_ratio,
            6,
        ),
        "areaDifferenceMvSeconds": (
            round(
                area_difference,
                8,
            )
            if area_difference
            is not None
            else None
        ),
        "polarityDifference": (
            polarity_difference
        ),
        "maximumSlopeDifferenceMvPerSecond": (
            round(
                slope_difference,
                4,
            )
            if slope_difference
            is not None
            else None
        ),
        "morphologyScore": round(
            difference_score,
            4,
        ),
        "morphologyGrade": _grade(
            difference_score
        ),
        "confidence": round(
            confidence,
            2,
        ),
    }


def analyze_morphology(
    beat_arrays: dict[
        str,
        dict[int, np.ndarray],
    ],
    reference_templates: dict[
        str,
        np.ndarray,
    ],
    trigger_index: int | None,
    sampling_rate_hz: float,
) -> dict[str, Any]:
    per_lead: dict[
        str,
        Any,
    ] = {}

    scores = []
    excluded = []

    for (
        lead_id,
        beats,
    ) in beat_arrays.items():
        result = compare_templates(
            (
                beats.get(
                    trigger_index
                )
                if isinstance(
                    trigger_index,
                    int,
                )
                else None
            ),
            reference_templates.get(
                lead_id
            ),
            sampling_rate_hz,
        )

        per_lead[lead_id] = result

        if (
            result.get("status")
            == "ready"
        ):
            scores.append(
                float(
                    result[
                        "morphologyScore"
                    ]
                )
            )
        else:
            excluded.append(
                lead_id
            )

    if scores:
        score = float(
            np.median(scores)
        )

        status = "ready"

        confidence = float(
            np.median(
                [
                    result.get(
                        "confidence",
                        0.0,
                    )
                    for result
                    in per_lead.values()
                    if result.get(
                        "status"
                    )
                    == "ready"
                ]
            )
        )

    else:
        score = 1.0
        status = "failed"
        confidence = 0.0

    best_lead = min(
        (
            lead
            for lead, result
            in per_lead.items()
            if result.get("status")
            == "ready"
        ),
        key=lambda lead: (
            per_lead[lead].get(
                (
                    "normalized"
                    "EuclideanDistance"
                ),
                999.0,
            )
        ),
        default=None,
    )

    return {
        "status": status,
        "leadResults": per_lead,
        "multiLeadMorphologyScore": (
            round(
                score,
                4,
            )
            if scores
            else None
        ),
        "morphologyGrade": (
            _grade(score)
            if scores
            else "insufficient"
        ),
        "morphologyConfidence": round(
            confidence,
            2,
        ),
        "excludedLeadIds": excluded,
        "insufficientReference": (
            not bool(
                reference_templates
            )
        ),
        "bestMorphologyLead": (
            best_lead
        ),
        "bestLeadSelectionReason": (
            "lowest normalized "
            "trigger-to-baseline "
            "template distance"
        ),
    }