from __future__ import annotations

from typing import Any

import numpy as np

from app.analysis.constants import (
    BEAT_MAX_INVALID_PERCENT,
    BEAT_POST_R_SECONDS,
    BEAT_PRE_R_SECONDS,
    REFERENCE_MAX_BEATS,
    REFERENCE_MIN_BEATS,
    REFERENCE_TRIGGER_EXCLUSION_BEATS,
)
from app.analysis.models import (
    BeatWindow,
)


def _annotation_samples(
    metadata: dict[str, Any],
    symbols: set[str],
) -> list[int]:
    output = []

    for item in (
        metadata.get("annotations")
        or []
    ):
        if str(
            item.get("symbol")
        ) not in symbols:
            continue

        value = item.get(
            "captureOffsetSamples"
        )

        if value is not None:
            output.append(
                int(value)
            )

    return output


def segment_beats(
    raw_leads: dict[
        str,
        np.ndarray,
    ],
    filtered_leads: dict[
        str,
        np.ndarray,
    ],
    r_peak_analysis: dict[
        str,
        Any,
    ],
    metadata: dict[str, Any],
    sampling_rate_hz: float,
) -> tuple[
    dict[str, Any],
    list[BeatWindow],
    dict[
        str,
        dict[int, np.ndarray],
    ],
]:
    peaks = [
        int(value)
        for value
        in r_peak_analysis.get(
            "rPeakSamples"
        )
        or []
    ]

    pre_samples = int(
        round(
            BEAT_PRE_R_SECONDS
            * sampling_rate_hz
        )
    )

    post_samples = int(
        round(
            BEAT_POST_R_SECONDS
            * sampling_rate_hz
        )
    )

    sample_count = min(
        (
            signal.size
            for signal
            in filtered_leads.values()
        ),
        default=0,
    )

    windows: list[
        BeatWindow
    ] = []

    beat_arrays: dict[
        str,
        dict[int, np.ndarray],
    ] = {
        lead: {}
        for lead in filtered_leads
    }

    beat_records = []

    for (
        beat_index,
        peak,
    ) in enumerate(peaks):
        start = peak - pre_samples
        end = (
            peak
            + post_samples
            + 1
        )

        complete = bool(
            start >= 0
            and end <= sample_count
        )

        invalid_by_lead = {}

        for (
            lead_id,
            raw,
        ) in raw_leads.items():
            clipped_start = max(
                0,
                start,
            )

            clipped_end = min(
                raw.size,
                end,
            )

            segment = raw[
                clipped_start:
                clipped_end
            ]

            invalid_percent = (
                100.0
                * int(
                    (
                        ~np.isfinite(
                            segment
                        )
                    ).sum()
                )
                / max(
                    segment.size,
                    1,
                )
            )

            invalid_by_lead[
                lead_id
            ] = round(
                invalid_percent,
                4,
            )

        window = BeatWindow(
            beat_index=beat_index,
            r_peak_sample=peak,
            start_sample=start,
            end_sample=end,
            complete=complete,
            invalid_percent_by_lead=(
                invalid_by_lead
            ),
        )

        windows.append(window)

        if complete:
            for (
                lead_id,
                signal,
            ) in filtered_leads.items():
                beat_arrays[
                    lead_id
                ][beat_index] = (
                    signal[
                        start:end
                    ].copy()
                )

        beat_records.append(
            {
                "beatIndex": (
                    beat_index
                ),
                "rPeakSample": peak,
                "startSample": start,
                "endSampleExclusive": (
                    end
                ),
                "boundaryComplete": (
                    complete
                ),
                "invalidPercentByLead": (
                    invalid_by_lead
                ),
            }
        )

    trigger_index = (
        r_peak_analysis.get(
            "triggerBeatIndex"
        )
    )

    abnormal_samples = (
        _annotation_samples(
            metadata,
            {
                "V",
                "A",
                "F",
                "R",
                "E",
                "j",
            },
        )
    )

    normal_samples = (
        _annotation_samples(
            metadata,
            {"N"},
        )
    )

    excluded: dict[
        int,
        list[str],
    ] = {
        index: []
        for index
        in range(len(peaks))
    }

    selected: list[int] = []

    reasons: dict[
        int,
        list[str],
    ] = {}

    for window in windows:
        index = window.beat_index
        local_reasons = []

        if not window.complete:
            local_reasons.append(
                "boundary_incomplete"
            )

        if any(
            value
            > BEAT_MAX_INVALID_PERCENT
            for value
            in window
            .invalid_percent_by_lead
            .values()
        ):
            local_reasons.append(
                "missing_data"
            )

        if (
            isinstance(
                trigger_index,
                int,
            )
            and abs(
                index - trigger_index
            )
            <= REFERENCE_TRIGGER_EXCLUSION_BEATS
        ):
            local_reasons.append(
                "trigger_or_adjacent_beat"
            )

        if (
            abnormal_samples
            and min(
                abs(
                    window.r_peak_sample
                    - sample
                )
                for sample
                in abnormal_samples
            )
            <= int(
                0.12
                * sampling_rate_hz
            )
        ):
            local_reasons.append(
                (
                    "annotation_triggered_"
                    "abnormal_beat"
                )
            )

        if local_reasons:
            excluded[index] = (
                local_reasons
            )

            continue

        normal_annotation_near = bool(
            normal_samples
            and min(
                abs(
                    window.r_peak_sample
                    - sample
                )
                for sample
                in normal_samples
            )
            <= int(
                0.12
                * sampling_rate_hz
            )
        )

        reasons[index] = [
            "complete_boundary",
            "acceptable_missing_data",
            "not_trigger_or_adjacent",
            (
                "near_INCART_N_annotation"
                if normal_annotation_near
                else (
                    "unlabeled_"
                    "nontrigger_candidate"
                )
            ),
        ]

        selected.append(index)

    if (
        len(selected)
        > REFERENCE_MAX_BEATS
    ):
        if isinstance(
            trigger_index,
            int,
        ):
            selected = sorted(
                selected,
                key=lambda index: abs(
                    index - trigger_index
                ),
            )[
                :REFERENCE_MAX_BEATS
            ]

            selected.sort()

        else:
            selected = selected[
                :REFERENCE_MAX_BEATS
            ]

    if (
        len(selected)
        >= REFERENCE_MIN_BEATS
    ):
        status = "ready"
    elif selected:
        status = "partial"
    else:
        status = "failed"

    trigger_record = (
        beat_records[trigger_index]
        if (
            isinstance(
                trigger_index,
                int,
            )
            and 0
            <= trigger_index
            < len(beat_records)
        )
        else None
    )

    return (
        {
            "status": status,
            "preRWindowSeconds": (
                BEAT_PRE_R_SECONDS
            ),
            "postRWindowSeconds": (
                BEAT_POST_R_SECONDS
            ),
            "preRWindowSamples": (
                pre_samples
            ),
            "postRWindowSamples": (
                post_samples
            ),
            "beatWindows": (
                beat_records
            ),
            "triggerBeat": (
                trigger_record
            ),
            "preTriggerBeatIndices": [
                index
                for index
                in range(len(peaks))
                if (
                    isinstance(
                        trigger_index,
                        int,
                    )
                    and index
                    < trigger_index
                )
            ],
            "postTriggerBeatIndices": [
                index
                for index
                in range(len(peaks))
                if (
                    isinstance(
                        trigger_index,
                        int,
                    )
                    and index
                    > trigger_index
                )
            ],
            "boundaryCompleteBeatCount": (
                sum(
                    1
                    for item
                    in beat_records
                    if item[
                        "boundaryComplete"
                    ]
                )
            ),
            "boundaryIncompleteBeatCount": (
                sum(
                    1
                    for item
                    in beat_records
                    if not item[
                        "boundaryComplete"
                    ]
                )
            ),
            "referenceBeatCandidateCount": (
                len(selected)
                + sum(
                    1
                    for values
                    in excluded.values()
                    if values
                )
            ),
            "selectedReferenceBeatIndices": (
                selected
            ),
            "selectedReferenceBeatSamplePositions": [
                peaks[index]
                for index in selected
            ],
            "referenceBeatSelectionReasons": {
                str(index): (
                    reasons.get(
                        index,
                        [],
                    )
                )
                for index in selected
            },
            "beatExclusionReasons": {
                str(index): values
                for index, values
                in excluded.items()
                if values
            },
            "baselineTemplateQuality": (
                "good"
                if len(selected) >= 5
                else (
                    "limited"
                    if selected
                    else "unavailable"
                )
            ),
            "insufficientReferenceBeats": (
                len(selected)
                < REFERENCE_MIN_BEATS
            ),
        },
        windows,
        beat_arrays,
    )


def build_reference_templates(
    beat_arrays: dict[
        str,
        dict[int, np.ndarray],
    ],
    selected_reference_indices: (
        list[int]
    ),
) -> dict[str, np.ndarray]:
    templates: dict[
        str,
        np.ndarray,
    ] = {}

    for (
        lead_id,
        beats,
    ) in beat_arrays.items():
        available = [
            beats[index]
            for index
            in selected_reference_indices
            if index in beats
        ]

        if available:
            templates[lead_id] = (
                np.median(
                    np.stack(
                        available,
                        axis=0,
                    ),
                    axis=0,
                )
            )

    return templates