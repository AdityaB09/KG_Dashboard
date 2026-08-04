from __future__ import annotations

from typing import Any

from app.analysis.constants import (
    BEAT_PRE_R_SECONDS,
    QRS_EDGE_FRACTION,
    QRS_MAX_DURATION_MS,
    QRS_MIN_DURATION_MS,
    QRS_SEARCH_POST_SECONDS,
    QRS_SEARCH_PRE_SECONDS,
)

from copy import deepcopy
from typing import Mapping

import numpy as np


def trapezoidal_integral(
    values: np.ndarray,
    *,
    dx: float,
) -> float:
    """
    Compute a trapezoidal integral across supported NumPy versions.

    NumPy 2.0 introduced ``numpy.trapezoid`` and later NumPy releases
    removed the deprecated ``numpy.trapz`` alias. Older local
    environments may still expose only ``numpy.trapz``.
    """
    current = getattr(
        np,
        "trapezoid",
        None,
    )

    if callable(current):
        return float(
            current(
                values,
                dx=dx,
            )
        )

    legacy = getattr(
        np,
        "trapz",
        None,
    )

    if callable(legacy):
        return float(
            legacy(
                values,
                dx=dx,
            )
        )

    raise RuntimeError(
        "The installed NumPy version provides neither "
        "numpy.trapezoid nor numpy.trapz."
    )


def measure_qrs(
    segment: np.ndarray | None,
    sampling_rate_hz: float,
) -> dict[str, Any]:
    if (
        segment is None
        or segment.size < 8
    ):
        return {
            "status": "failed",
            "failureReason": (
                "missing_or_short_beat"
            ),
            "confidence": 0.0,
        }

    r_expected = int(
        round(
            BEAT_PRE_R_SECONDS
            * sampling_rate_hz
        )
    )

    pre = int(
        round(
            QRS_SEARCH_PRE_SECONDS
            * sampling_rate_hz
        )
    )

    post = int(
        round(
            QRS_SEARCH_POST_SECONDS
            * sampling_rate_hz
        )
    )

    start = max(
        0,
        r_expected - pre,
    )

    end = min(
        segment.size,
        r_expected + post + 1,
    )

    window = np.asarray(
        segment[start:end],
        dtype=np.float64,
    )

    if (
        window.size < 5
        or not np.all(
            np.isfinite(window)
        )
    ):
        return {
            "status": "failed",
            "failureReason": (
                "invalid_qrs_window"
            ),
            "confidence": 0.0,
        }

    baseline_region = (
        np.concatenate(
            (
                segment[
                    :max(1, start)
                ],
                segment[
                    min(
                        segment.size,
                        end,
                    ):
                ],
            )
        )
    )

    baseline = (
        float(
            np.median(
                baseline_region
            )
        )
        if baseline_region.size
        else float(
            np.median(window)
        )
    )

    centered = (
        window - baseline
    )

    peak_local = int(
        np.argmax(
            np.abs(centered)
        )
    )

    peak_amplitude = float(
        centered[peak_local]
    )

    absolute_peak = abs(
        peak_amplitude
    )

    if absolute_peak < 0.04:
        return {
            "status": "failed",
            "failureReason": (
                "qrs_amplitude_too_low"
            ),
            "confidence": 0.0,
        }

    threshold = max(
        0.015,
        QRS_EDGE_FRACTION
        * absolute_peak,
    )

    onset_local = None

    for index in range(
        peak_local,
        0,
        -1,
    ):
        if (
            abs(centered[index])
            <= threshold
            and abs(
                centered[index - 1]
            )
            <= threshold
        ):
            onset_local = index
            break

    offset_local = None

    for index in range(
        peak_local,
        window.size - 1,
    ):
        if (
            abs(centered[index])
            <= threshold
            and abs(
                centered[index + 1]
            )
            <= threshold
        ):
            offset_local = index
            break

    if onset_local is None:
        return {
            "status": "failed",
            "failureReason": (
                "missing_qrs_onset"
            ),
            "confidence": 0.0,
        }

    if offset_local is None:
        return {
            "status": "failed",
            "failureReason": (
                "missing_qrs_offset"
            ),
            "confidence": 0.0,
        }

    duration_samples = int(
        offset_local
        - onset_local
        + 1
    )

    duration_ms = (
        1000.0
        * duration_samples
        / sampling_rate_hz
    )

    qrs = centered[
        onset_local:
        offset_local + 1
    ]

    gradients = (
        np.diff(qrs)
        * sampling_rate_hz
        if qrs.size > 1
        else np.asarray([0.0])
    )

    r_wave = float(
        np.max(qrs)
    )

    s_wave = float(
        np.min(qrs)
    )

    if (
        abs(r_wave)
        > abs(s_wave) * 1.15
    ):
        polarity = "positive"
    elif (
        abs(s_wave)
        > abs(r_wave) * 1.15
    ):
        polarity = "negative"
    else:
        polarity = "biphasic"

    physiological = bool(
        QRS_MIN_DURATION_MS
        <= duration_ms
        <= QRS_MAX_DURATION_MS
    )

    edge_margin = min(
        peak_local - onset_local,
        offset_local - peak_local,
    )

    confidence = min(
        100.0,
        50.0
        + 25.0
        * min(
            1.0,
            absolute_peak / 0.5,
        )
        + 25.0
        * min(
            1.0,
            edge_margin
            / max(
                1,
                int(
                    0.02
                    * sampling_rate_hz
                ),
            ),
        ),
    )

    if not physiological:
        confidence *= 0.65

    return {
        "status": "ready",
        "failureReason": None,
        "qrsOnsetSampleInBeat": int(
            start + onset_local
        ),
        "qrsPeakSampleInBeat": int(
            start + peak_local
        ),
        "qrsOffsetSampleInBeat": int(
            start + offset_local
        ),
        "qrsDurationSamples": (
            duration_samples
        ),
        "qrsDurationMilliseconds": round(
            duration_ms,
            3,
        ),
        "rWaveAmplitudeMv": round(
            r_wave,
            6,
        ),
        "sWaveAmplitudeMv": round(
            s_wave,
            6,
        ),
        "peakToPeakAmplitudeMv": round(
            r_wave - s_wave,
            6,
        ),
        "qrsAreaMvSeconds": round(
            trapezoidal_integral(
                np.abs(qrs),
                dx=(
                    1.0
                    / sampling_rate_hz
                ),
            ),
            8,
        ),
        "qrsPolarity": polarity,
        "maximumUpstrokeMvPerSecond": (
            round(
                float(
                    np.max(
                        gradients
                    )
                ),
                4,
            )
        ),
        "maximumDownstrokeMvPerSecond": (
            round(
                float(
                    np.min(
                        gradients
                    )
                ),
                4,
            )
        ),
        "durationWithinMeasurementBounds": (
            physiological
        ),
        "confidence": round(
            confidence,
            2,
        ),
    }


def analyze_qrs(
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
    per_lead = {}
    trigger_durations = []
    baseline_durations = []

    for (
        lead_id,
        beats,
    ) in beat_arrays.items():
        trigger = measure_qrs(
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
            sampling_rate_hz,
        )

        baseline = measure_qrs(
            reference_templates.get(
                lead_id
            ),
            sampling_rate_hz,
        )

        difference = None

        if (
            trigger.get(
                "qrsDurationMilliseconds"
            )
            is not None
            and baseline.get(
                "qrsDurationMilliseconds"
            )
            is not None
        ):
            difference = (
                trigger[
                    "qrsDurationMilliseconds"
                ]
                - baseline[
                    "qrsDurationMilliseconds"
                ]
            )

            trigger_durations.append(
                trigger[
                    "qrsDurationMilliseconds"
                ]
            )

            baseline_durations.append(
                baseline[
                    "qrsDurationMilliseconds"
                ]
            )

        per_lead[lead_id] = {
            "triggerBeat": trigger,
            "baselineTemplate": (
                baseline
            ),
            "widthDifferenceMilliseconds": (
                round(
                    difference,
                    3,
                )
                if difference
                is not None
                else None
            ),
        }

    if trigger_durations:
        median_trigger = float(
            np.median(
                trigger_durations
            )
        )

        median_baseline = float(
            np.median(
                baseline_durations
            )
        )

        duration_mad = float(
            np.median(
                np.abs(
                    np.asarray(
                        trigger_durations
                    )
                    - median_trigger
                )
            )
        )

        agreement = max(
            0.0,
            1.0
            - duration_mad / 30.0,
        )

        status = "ready"

    else:
        median_trigger = None
        median_baseline = None
        agreement = 0.0
        status = "failed"

    best_lead = max(
        per_lead,
        key=lambda lead: (
            per_lead[lead][
                "triggerBeat"
            ].get(
                "confidence",
                0.0,
            )
        ),
        default=None,
    )

    return {
        "status": status,
        "leadResults": per_lead,
        "multiLeadMedianTriggerQrsDurationMilliseconds": (
            round(
                median_trigger,
                3,
            )
            if median_trigger
            is not None
            else None
        ),
        "multiLeadMedianBaselineQrsDurationMilliseconds": (
            round(
                median_baseline,
                3,
            )
            if median_baseline
            is not None
            else None
        ),
        "multiLeadMedianWidthDifferenceMilliseconds": (
            round(
                (
                    median_trigger
                    - median_baseline
                ),
                3,
            )
            if (
                median_trigger
                is not None
                and median_baseline
                is not None
            )
            else None
        ),
        "interLeadDurationAgreement": (
            round(
                agreement,
                4,
            )
        ),
        "bestQrsWidthLead": (
            best_lead
        ),
        "bestLeadSelectionReason": (
            "highest trigger-beat "
            "QRS measurement confidence"
        ),
    }
    
def _grade(score: float) -> str:
    if score >= 80.0:
        return "high"
    if score >= 60.0:
        return "moderate"
    if score >= 35.0:
        return "low"
    return "insufficient"


def calibrate_qrs_confidence(qrs_analysis: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(qrs_analysis))
    if result.get("status") == "failed":
        result["measurementConfidence"] = 0.0
        result["measurementConfidenceGrade"] = "insufficient"
        return result

    lead_confidences: list[float] = []
    for lead_result in (result.get("leadResults") or {}).values():
        trigger = lead_result.get("triggerBeat") or {}
        confidence = trigger.get("confidence")
        if confidence is not None and np.isfinite(float(confidence)):
            lead_confidences.append(float(confidence))

    base = float(np.median(lead_confidences)) if lead_confidences else 70.0
    agreement = float(result.get("interLeadDurationAgreement") or 0.0)
    limitations = list(result.get("limitations") or [])

    if agreement >= 0.75:
        cap = 100.0
    elif agreement >= 0.50:
        cap = 75.0
        limitations.append("QRS duration has only moderate inter-lead agreement.")
    elif agreement >= 0.30:
        cap = 50.0
        result["status"] = "partial"
        limitations.append("QRS duration has low inter-lead agreement and must be interpreted cautiously.")
    else:
        cap = 30.0
        result["status"] = "partial"
        limitations.append("QRS duration has very low inter-lead agreement; the aggregate width is insufficient for a high-confidence conclusion.")

    calibrated = min(base, cap, max(0.0, agreement * 100.0 + 20.0))
    result["measurementConfidence"] = round(calibrated, 2)
    result["measurementConfidenceGrade"] = _grade(calibrated)
    result["confidenceBasis"] = {
        "medianPerLeadTriggerConfidence": round(base, 2),
        "interLeadDurationAgreement": round(agreement, 4),
        "agreementConfidenceCap": cap,
    }
    result["limitations"] = list(dict.fromkeys(limitations))
    return result