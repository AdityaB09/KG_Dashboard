from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from app.analysis.constants import (
    MORPHOLOGY_ABNORMAL_SCORE,
    QRS_WIDE_EVIDENCE_MS,
    QRS_WIDTH_DIFFERENCE_EVIDENCE_MS,
)
from app.analysis.morphology import (
    compare_templates,
)


def _patterns(
    candidate_indices: list[int],
    total_beats: int,
) -> dict[str, Any]:
    candidate_set = set(
        candidate_indices
    )

    groups = []
    current = []

    for index in sorted(
        candidate_indices
    ):
        if (
            current
            and index
            != current[-1] + 1
        ):
            groups.append(current)
            current = []

        current.append(index)

    if current:
        groups.append(current)

    isolated = sum(
        1
        for group in groups
        if len(group) == 1
    )

    couplets = sum(
        1
        for group in groups
        if len(group) == 2
    )

    triplets = sum(
        1
        for group in groups
        if len(group) == 3
    )

    runs = [
        {
            "startBeatIndex": (
                group[0]
            ),
            "endBeatIndex": (
                group[-1]
            ),
            "length": len(group),
        }
        for group in groups
        if len(group) >= 4
    ]

    bigeminy = False
    trigeminy = False

    for start in range(
        max(
            0,
            total_beats - 7,
        )
    ):
        window = [
            index in candidate_set
            for index in range(
                start,
                min(
                    total_beats,
                    start + 8,
                ),
            )
        ]

        if (
            len(window) >= 6
            and (
                all(
                    window[offset]
                    for offset
                    in range(
                        0,
                        len(window),
                        2,
                    )
                )
                or all(
                    window[offset]
                    for offset
                    in range(
                        1,
                        len(window),
                        2,
                    )
                )
            )
        ):
            bigeminy = True

        if (
            len(window) >= 6
            and any(
                all(
                    window[offset]
                    for offset
                    in range(
                        seed,
                        len(window),
                        3,
                    )
                )
                for seed in range(3)
            )
        ):
            trigeminy = True

    return {
        "isolatedEvents": isolated,
        "couplets": couplets,
        "triplets": triplets,
        "runs": runs,
        "bigeminyDetected": (
            bigeminy
        ),
        "trigeminyDetected": (
            trigeminy
        ),
    }


def analyze_episode_ectopy(
    metadata: dict[str, Any],
    rr: dict[str, Any],
    qrs: dict[str, Any],
    morphology: dict[str, Any],
    beat_arrays: dict[
        str,
        dict[int, np.ndarray],
    ],
    reference_templates: dict[
        str,
        np.ndarray,
    ],
    total_beats: int,
    trigger_index: int | None,
    sampling_rate_hz: float,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
]:
    trigger_qrs = qrs.get(
        (
            "multiLeadMedian"
            "TriggerQrsDuration"
            "Milliseconds"
        )
    )

    baseline_qrs = qrs.get(
        (
            "multiLeadMedian"
            "BaselineQrsDuration"
            "Milliseconds"
        )
    )

    width_difference = qrs.get(
        (
            "multiLeadMedian"
            "WidthDifference"
            "Milliseconds"
        )
    )

    morphology_score = (
        morphology.get(
            "multiLeadMorphologyScore"
        )
    )

    polarity_changes = sum(
        1
        for result
        in morphology.get(
            "leadResults",
            {},
        ).values()
        if result.get(
            "polarityDifference"
        )
        is True
    )

    evidence_items = {
        "prematureTiming": bool(
            rr.get(
                "prematureTimingEvidence"
            )
        ),
        "triggerQrsAtLeast120Ms": (
            trigger_qrs is not None
            and trigger_qrs
            >= QRS_WIDE_EVIDENCE_MS
        ),
        "qrsWiderThanBaseline": (
            width_difference is not None
            and width_difference
            >= QRS_WIDTH_DIFFERENCE_EVIDENCE_MS
        ),
        "markedMorphologyDifference": (
            morphology_score is not None
            and morphology_score
            >= MORPHOLOGY_ABNORMAL_SCORE
        ),
        "compensatoryPause": (
            rr.get(
                "compensatoryPauseStatus"
            )
            in {
                "full",
                "incomplete",
            }
        ),
        "polarityChange": (
            polarity_changes > 0
        ),
    }

    weights = {
        "prematureTiming": 20.0,
        "triggerQrsAtLeast120Ms": (
            20.0
        ),
        "qrsWiderThanBaseline": 15.0,
        "markedMorphologyDifference": (
            25.0
        ),
        "compensatoryPause": 15.0,
        "polarityChange": 5.0,
    }

    evidence_score = sum(
        weights[key]
        for key, present
        in evidence_items.items()
        if present
    )

    measurable_count = sum(
        value is not None
        for value in [
            rr.get("couplingRatio"),
            trigger_qrs,
            baseline_qrs,
            morphology_score,
        ]
    )

    if measurable_count < 2:
        evidence_grade = (
            "insufficient_"
            "measurable_evidence"
        )

    elif evidence_score >= 65:
        evidence_grade = "strong"

    elif evidence_score >= 40:
        evidence_grade = "moderate"

    elif evidence_score >= 20:
        evidence_grade = "limited"

    else:
        evidence_grade = (
            "not_supported"
        )

    candidate_votes: Counter[
        int
    ] = Counter()

    candidate_details: dict[
        int,
        list[float],
    ] = {}

    for (
        lead_id,
        beats,
    ) in beat_arrays.items():
        baseline = (
            reference_templates.get(
                lead_id
            )
        )

        if baseline is None:
            continue

        for (
            beat_index,
            beat,
        ) in beats.items():
            result = compare_templates(
                beat,
                baseline,
                sampling_rate_hz,
            )

            if (
                result.get("status")
                != "ready"
            ):
                continue

            score = float(
                result[
                    "morphologyScore"
                ]
            )

            candidate_details.setdefault(
                beat_index,
                [],
            ).append(score)

            if (
                score
                >= MORPHOLOGY_ABNORMAL_SCORE
            ):
                candidate_votes[
                    beat_index
                ] += 1

    minimum_leads = min(
        2,
        max(
            1,
            len(
                reference_templates
            ),
        ),
    )

    candidates = sorted(
        index
        for index, votes
        in candidate_votes.items()
        if votes >= minimum_leads
    )

    patterns = _patterns(
        candidates,
        total_beats,
    )

    reference_v_count = int(
        (
            metadata.get(
                "annotationCounts"
            )
            or {}
        ).get(
            "V",
            0,
        )
    )

    burden = (
        100.0
        * len(candidates)
        / max(total_beats, 1)
    )

    rounded_groups = Counter(
        tuple(
            round(value, 1)
            for value
            in sorted(values)[:3]
        )
        for index, values
        in candidate_details.items()
        if index in candidates
    )

    repeated_morphology = any(
        count >= 2
        for count
        in rounded_groups.values()
    )

    ectopic_burden = {
        "status": (
            "ready"
            if total_beats
            else "failed"
        ),
        "totalDetectedBeats": (
            total_beats
        ),
        "annotationDerived": {
            "referenceVAnnotationCount": (
                reference_v_count
            ),
            "source": (
                "PhysioNet INCART atr "
                "reference annotations"
            ),
        },
        "independentlyMeasured": {
            "abnormalMorphologyCandidateCount": (
                len(candidates)
            ),
            "candidateBeatIndices": (
                candidates
            ),
            "minimumAgreeingLeadCount": (
                minimum_leads
            ),
            "ectopicBurdenPercent": round(
                burden,
                3,
            ),
        },
        **patterns,
        "repeatedMorphology": (
            repeated_morphology
        ),
        "morphologyGroupCount": (
            len(rounded_groups)
        ),
        "patternConfidence": (
            round(
                min(
                    100.0,
                    (
                        20.0
                        + 10.0
                        * total_beats
                        + 5.0
                        * len(
                            reference_templates
                        )
                    ),
                ),
                2,
            )
            if total_beats
            else 0.0
        ),
    }

    if evidence_grade in {
        "strong",
        "moderate",
    }:
        reference_consistency = (
            "deterministic evidence "
            "supports the reference label"
        )
    elif evidence_grade == "limited":
        reference_consistency = (
            "evidence is limited by "
            "signal quality"
        )
    else:
        reference_consistency = (
            "insufficient measurable "
            "evidence"
        )

    ventricular_evidence = {
        "status": (
            "ready"
            if measurable_count >= 2
            else "partial"
        ),
        "referenceAnnotation": (
            "INCART V"
        ),
        "datasetAnnotationSample": None,
        "prematureTiming": rr.get(
            "prematureTimingEvidence"
        ),
        "couplingIntervalMilliseconds": (
            rr.get(
                (
                    "triggerCoupling"
                    "IntervalMilliseconds"
                )
            )
        ),
        "couplingRatio": rr.get(
            "couplingRatio"
        ),
        "triggerQrsDurationMilliseconds": (
            trigger_qrs
        ),
        "baselineQrsDurationMilliseconds": (
            baseline_qrs
        ),
        "qrsWidthDifferenceMilliseconds": (
            width_difference
        ),
        "triggerToBaselineMorphologyDifference": (
            morphology_score
        ),
        "compensatoryPauseStatus": (
            rr.get(
                "compensatoryPauseStatus"
            )
        ),
        "postTriggerPauseMilliseconds": (
            rr.get(
                "postTriggerPauseMilliseconds"
            )
        ),
        "polarityChangeLeadCount": (
            polarity_changes
        ),
        "repeatedTriggerMorphology": (
            repeated_morphology
        ),
        "evidenceItems": evidence_items,
        "evidenceScore": round(
            evidence_score,
            2,
        ),
        "evidenceGrade": (
            evidence_grade
        ),
        "consistencyWithReferenceAnnotation": (
            reference_consistency
        ),
        "isIndependentDiagnosis": False,
    }

    return (
        ventricular_evidence,
        ectopic_burden,
    )