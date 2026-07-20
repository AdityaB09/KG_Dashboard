from __future__ import annotations

from typing import Any

import numpy as np


def analyze_lead_agreement(
    quality: dict[str, Any],
    r_peaks: dict[str, Any],
    qrs: dict[str, Any],
    morphology: dict[str, Any],
) -> dict[str, Any]:
    usable = list(
        quality.get(
            "overall",
            {},
        ).get(
            "usableLeadIds",
            [],
        )
        or []
    )

    excluded = list(
        quality.get(
            "overall",
            {},
        ).get(
            "excludedLeadIds",
            [],
        )
        or []
    )

    r_agreement = float(
        r_peaks.get(
            "rPeakAgreement",
            0.0,
        )
    )

    qrs_agreement = float(
        qrs.get(
            "interLeadDurationAgreement",
            0.0,
        )
    )

    morphology_scores = [
        float(
            result[
                "morphologyScore"
            ]
        )
        for result
        in morphology.get(
            "leadResults",
            {},
        ).values()
        if result.get("status")
        == "ready"
    ]

    morphology_agreement = (
        max(
            0.0,
            1.0
            - float(
                np.std(
                    morphology_scores
                )
            )
            / 0.25,
        )
        if morphology_scores
        else 0.0
    )

    polarities = [
        result.get(
            "triggerBeat",
            {},
        ).get(
            "qrsPolarity"
        )
        for result
        in qrs.get(
            "leadResults",
            {},
        ).values()
        if result.get(
            "triggerBeat",
            {},
        ).get(
            "status"
        )
        == "ready"
    ]

    polarity_agreement = 0.0

    if polarities:
        polarity_agreement = (
            max(
                polarities.count(
                    value
                )
                for value
                in set(polarities)
            )
            / len(polarities)
        )

    conflicting = []

    median_morph = (
        float(
            np.median(
                morphology_scores
            )
        )
        if morphology_scores
        else None
    )

    for (
        lead_id,
        result,
    ) in morphology.get(
        "leadResults",
        {},
    ).items():
        if (
            result.get("status")
            != "ready"
        ):
            continue

        if (
            median_morph is not None
            and abs(
                float(
                    result[
                        "morphologyScore"
                    ]
                )
                - median_morph
            )
            > 0.30
        ):
            conflicting.append(
                lead_id
            )

    score = 100.0 * (
        0.35 * r_agreement
        + 0.25 * qrs_agreement
        + 0.25
        * morphology_agreement
        + 0.15
        * polarity_agreement
    )

    return {
        "status": (
            "ready"
            if usable
            else "failed"
        ),
        "usableLeadCount": (
            len(usable)
        ),
        "excludedLeadCount": (
            len(excluded)
        ),
        "usableLeadIds": usable,
        "excludedLeadIds": excluded,
        "rPeakAgreement": round(
            r_agreement,
            4,
        ),
        "triggerAlignmentAgreement": (
            round(
                r_agreement,
                4,
            )
        ),
        "qrsWidthAgreement": round(
            qrs_agreement,
            4,
        ),
        "morphologyAgreement": round(
            morphology_agreement,
            4,
        ),
        "polarityAgreement": round(
            polarity_agreement,
            4,
        ),
        "conflictingLeadIds": sorted(
            set(conflicting)
        ),
        "bestTimingLead": (
            r_peaks.get(
                "primaryTimingLead"
            )
        ),
        "bestQrsWidthLead": (
            qrs.get(
                "bestQrsWidthLead"
            )
        ),
        "bestMorphologyLead": (
            morphology.get(
                "bestMorphologyLead"
            )
        ),
        "leadSelectionReasons": {
            "timing": (
                r_peaks.get(
                    "leadSelectionReason"
                )
            ),
            "qrsWidth": (
                qrs.get(
                    (
                        "bestLead"
                        "SelectionReason"
                    )
                )
            ),
            "morphology": (
                morphology.get(
                    (
                        "bestLead"
                        "SelectionReason"
                    )
                )
            ),
        },
        "overallMultiLeadAgreementScore": (
            round(
                score,
                2,
            )
        ),
    }