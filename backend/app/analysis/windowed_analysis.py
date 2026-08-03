from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np


WINDOWED_SCHEMA_VERSION = "phase6-windowed-analysis-v1"

_EVENT_HR_INVALID_DIAGNOSIS_MARKERS = (
    "ventricular fibrillation",
    "vfib",
)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    return number if math.isfinite(number) else None


def _round(
    value: Any,
    digits: int = 3,
) -> float | None:
    number = _number(value)
    return None if number is None else round(number, digits)


def _window(
    *,
    name: str,
    start_seconds: float,
    end_seconds: float,
    sample_rate_hz: float,
    total_samples: int,
) -> dict[str, Any]:
    start = max(0.0, float(start_seconds))
    end = max(start, float(end_seconds))
    start_sample = min(
        total_samples,
        max(
            0,
            int(round(start * sample_rate_hz)),
        ),
    )
    end_sample = min(
        total_samples,
        max(
            start_sample,
            int(round(end * sample_rate_hz)),
        ),
    )

    return {
        "name": name,
        "startSeconds": round(
            start_sample / sample_rate_hz,
            3,
        ),
        "endSeconds": round(
            end_sample / sample_rate_hz,
            3,
        ),
        "startSample": start_sample,
        "endSample": end_sample,
        "sampleCount": max(
            0,
            end_sample - start_sample,
        ),
    }


def measurement_windows(
    metadata: Mapping[str, Any],
    *,
    sample_rate_hz: float,
    total_samples: int,
) -> dict[str, dict[str, Any]]:
    duration_seconds = (
        total_samples / sample_rate_hz
    )

    capture_start = (
        _number(
            metadata.get(
                "captureStartSeconds"
            )
        )
        or 0.0
    )
    capture_end = (
        _number(
            metadata.get(
                "captureEndSeconds"
            )
        )
        or _number(
            metadata.get(
                "durationSeconds"
            )
        )
        or duration_seconds
    )
    event_start = (
        _number(
            metadata.get(
                "eventStartOffsetSeconds"
            )
        )
        or _number(
            metadata.get(
                "eventStartSeconds"
            )
        )
    )
    event_end = (
        _number(
            metadata.get(
                "eventEndOffsetSeconds"
            )
        )
        or _number(
            metadata.get(
                "eventEndSeconds"
            )
        )
    )

    if event_start is None:
        event_start = min(
            capture_end,
            _number(
                metadata.get(
                    "preSecondsCaptured"
                )
            )
            or capture_start,
        )

    if event_end is None:
        event_duration = (
            _number(
                metadata.get(
                    "eventDurationSeconds"
                )
            )
            or 0.0
        )
        event_end = min(
            capture_end,
            event_start + event_duration,
        )

    event_start = min(
        max(
            event_start,
            capture_start,
        ),
        capture_end,
    )
    event_end = min(
        max(
            event_end,
            event_start,
        ),
        capture_end,
    )

    return {
        "fullCapture": _window(
            name="full_capture",
            start_seconds=capture_start,
            end_seconds=capture_end,
            sample_rate_hz=sample_rate_hz,
            total_samples=total_samples,
        ),
        "preEvent": _window(
            name="pre_event",
            start_seconds=capture_start,
            end_seconds=event_start,
            sample_rate_hz=sample_rate_hz,
            total_samples=total_samples,
        ),
        "controlledEvent": _window(
            name="controlled_event",
            start_seconds=event_start,
            end_seconds=event_end,
            sample_rate_hz=sample_rate_hz,
            total_samples=total_samples,
        ),
        "postEvent": _window(
            name="post_event",
            start_seconds=event_end,
            end_seconds=capture_end,
            sample_rate_hz=sample_rate_hz,
            total_samples=total_samples,
        ),
    }


def _moving_average(
    values: np.ndarray,
    width: int,
) -> np.ndarray:
    width = max(
        1,
        min(
            int(width),
            int(values.size),
        ),
    )

    if width <= 1:
        return values.copy()

    kernel = np.ones(
        width,
        dtype=np.float64,
    ) / width

    return np.convolve(
        values,
        kernel,
        mode="same",
    )


def _condition_signal(
    values: np.ndarray,
    sample_rate_hz: float,
) -> np.ndarray:
    signal = np.asarray(
        values,
        dtype=np.float64,
    )
    finite = np.isfinite(signal)

    if not finite.any():
        return np.zeros_like(signal)

    if not finite.all():
        indexes = np.arange(
            signal.size,
            dtype=np.float64,
        )
        signal = np.interp(
            indexes,
            indexes[finite],
            signal[finite],
        )

    baseline_width = max(
        3,
        int(
            round(
                0.20 * sample_rate_hz
            )
        ),
    )
    conditioned = (
        signal
        - _moving_average(
            signal,
            baseline_width,
        )
    )

    scale = float(
        np.quantile(
            np.abs(
                conditioned
                - np.median(
                    conditioned
                )
            ),
            0.995,
        )
        or 0.0
    )

    if not math.isfinite(scale) or scale <= 1e-9:
        return np.zeros_like(
            conditioned
        )

    return conditioned / scale


def _peak_candidates(
    values: np.ndarray,
    sample_rate_hz: float,
) -> np.ndarray:
    conditioned = _condition_signal(
        values,
        sample_rate_hz,
    )

    if conditioned.size < max(
        8,
        int(sample_rate_hz),
    ):
        return np.empty(
            0,
            dtype=np.int64,
        )

    derivative = np.abs(
        np.diff(
            conditioned,
            prepend=conditioned[0],
        )
    )
    energy_width = max(
        1,
        int(
            round(
                0.055 * sample_rate_hz
            )
        ),
    )
    energy = _moving_average(
        derivative,
        energy_width,
    )

    median = float(
        np.median(energy)
    )
    mad = float(
        np.median(
            np.abs(
                energy - median
            )
        )
    )
    threshold = max(
        median + 4.0 * mad,
        float(
            np.quantile(
                energy,
                0.82,
            )
        ),
        0.03,
    )

    local = np.flatnonzero(
        (
            energy[1:-1]
            >= energy[:-2]
        )
        & (
            energy[1:-1]
            > energy[2:]
        )
        & (
            energy[1:-1]
            >= threshold
        )
    ) + 1

    if not local.size:
        return np.empty(
            0,
            dtype=np.int64,
        )

    refractory = max(
        1,
        int(
            round(
                0.22 * sample_rate_hz
            )
        ),
    )

    selected: list[int] = []

    for candidate in local.tolist():
        if (
            not selected
            or candidate
            - selected[-1]
            >= refractory
        ):
            selected.append(candidate)
            continue

        if (
            energy[candidate]
            > energy[selected[-1]]
        ):
            selected[-1] = candidate

    return np.asarray(
        selected,
        dtype=np.int64,
    )


def _rate_measurement(
    values: np.ndarray,
    sample_rate_hz: float,
) -> dict[str, Any]:
    peaks = _peak_candidates(
        values,
        sample_rate_hz,
    )

    if peaks.size < 3:
        return {
            "medianBpm": None,
            "rangeBpm": None,
            "measurementValid": False,
            "confidenceGrade": "insufficient",
            "reason": (
                "Fewer than three reliable "
                "QRS-energy candidates were detected."
            ),
            "detectedPeakCount": int(
                peaks.size
            ),
            "rrCoefficientOfVariation": None,
        }

    rr_seconds = (
        np.diff(peaks)
        / sample_rate_hz
    )
    valid_rr = rr_seconds[
        (
            rr_seconds >= 0.20
        )
        & (
            rr_seconds <= 2.50
        )
    ]

    if valid_rr.size < 2:
        return {
            "medianBpm": None,
            "rangeBpm": None,
            "measurementValid": False,
            "confidenceGrade": "insufficient",
            "reason": (
                "Too few physiologically usable "
                "R-R intervals were detected."
            ),
            "detectedPeakCount": int(
                peaks.size
            ),
            "rrCoefficientOfVariation": None,
        }

    rates = 60.0 / valid_rr
    median_rate = float(
        np.median(rates)
    )
    rr_cv = float(
        np.std(valid_rr)
        / max(
            np.mean(valid_rr),
            1e-9,
        )
    )

    valid = bool(
        20.0 <= median_rate <= 300.0
        and rr_cv <= 0.45
    )

    if not valid:
        reason = (
            "The candidate intervals did not "
            "form a sufficiently organized "
            "beat sequence."
        )
        grade = "low"
    elif valid_rr.size >= 6 and rr_cv <= 0.15:
        reason = None
        grade = "high"
    elif valid_rr.size >= 3 and rr_cv <= 0.30:
        reason = None
        grade = "moderate"
    else:
        reason = None
        grade = "low"

    return {
        "medianBpm": (
            round(
                median_rate,
                3,
            )
            if valid
            else None
        ),
        "rangeBpm": (
            [
                round(
                    float(
                        np.min(rates)
                    ),
                    3,
                ),
                round(
                    float(
                        np.max(rates)
                    ),
                    3,
                ),
            ]
            if valid
            else None
        ),
        "measurementValid": valid,
        "confidenceGrade": grade,
        "reason": reason,
        "detectedPeakCount": int(
            peaks.size
        ),
        "validIntervalCount": int(
            valid_rr.size
        ),
        "rrCoefficientOfVariation": round(
            rr_cv,
            4,
        ),
        "peakSamples": peaks.tolist(),
    }


def _qrs_measurement(
    values: np.ndarray,
    sample_rate_hz: float,
    rate_measurement: Mapping[str, Any],
) -> dict[str, Any]:
    peaks = np.asarray(
        rate_measurement.get(
            "peakSamples"
        )
        or [],
        dtype=np.int64,
    )
    conditioned = np.abs(
        _condition_signal(
            values,
            sample_rate_hz,
        )
    )

    widths: list[float] = []
    search = max(
        2,
        int(
            round(
                0.18
                * sample_rate_hz
            )
        ),
    )

    for peak in peaks:
        if (
            peak <= 1
            or peak
            >= conditioned.size - 2
        ):
            continue

        amplitude = float(
            conditioned[peak]
        )
        if amplitude <= 0.05:
            continue

        threshold = max(
            0.15,
            0.35 * amplitude,
        )
        left = int(peak)
        right = int(peak)

        while (
            left > max(
                0,
                peak - search,
            )
            and conditioned[left]
            > threshold
        ):
            left -= 1

        while (
            right
            < min(
                conditioned.size - 1,
                peak + search,
            )
            and conditioned[right]
            > threshold
        ):
            right += 1

        width_ms = (
            (
                right - left
            )
            / sample_rate_hz
            * 1000.0
        )

        if 25.0 <= width_ms <= 260.0:
            widths.append(
                width_ms
            )

    if len(widths) < 2:
        return {
            "medianMs": None,
            "rangeMs": None,
            "measurementValid": False,
            "confidenceGrade": "insufficient",
            "reason": (
                "QRS duration could not be "
                "measured reliably in this window."
            ),
            "measuredBeatCount": len(widths),
        }

    median_width = float(
        np.median(widths)
    )
    dispersion = float(
        np.std(widths)
    )

    if dispersion <= 15.0 and len(widths) >= 5:
        grade = "high"
    elif dispersion <= 30.0:
        grade = "moderate"
    else:
        grade = "low"

    return {
        "medianMs": round(
            median_width,
            3,
        ),
        "rangeMs": [
            round(
                float(
                    np.min(widths)
                ),
                3,
            ),
            round(
                float(
                    np.max(widths)
                ),
                3,
            ),
        ],
        "measurementValid": True,
        "confidenceGrade": grade,
        "reason": None,
        "measuredBeatCount": len(widths),
        "dispersionMs": round(
            dispersion,
            3,
        ),
    }


def _robust_amplitude(
    values: np.ndarray,
) -> float | None:
    signal = np.asarray(
        values,
        dtype=np.float64,
    )
    signal = signal[
        np.isfinite(signal)
    ]

    if signal.size < 4:
        return None

    centered = signal - np.median(
        signal
    )

    amplitude = float(
        np.quantile(
            np.abs(centered),
            0.95,
        )
    )

    return (
        amplitude
        if math.isfinite(amplitude)
        else None
    )


def _difference_score(
    first: np.ndarray,
    second: np.ndarray,
) -> float | None:
    first_amplitude = _robust_amplitude(
        first
    )
    second_amplitude = _robust_amplitude(
        second
    )

    if (
        first_amplitude is None
        or second_amplitude is None
        or first_amplitude <= 1e-9
        or second_amplitude <= 1e-9
    ):
        return None

    amplitude_component = abs(
        math.log(
            second_amplitude
            / first_amplitude
        )
    )

    first_diff = np.diff(
        np.asarray(
            first,
            dtype=np.float64,
        )
    )
    second_diff = np.diff(
        np.asarray(
            second,
            dtype=np.float64,
        )
    )

    first_slope = _robust_amplitude(
        first_diff
    )
    second_slope = _robust_amplitude(
        second_diff
    )

    slope_component = 0.0

    if (
        first_slope is not None
        and second_slope is not None
        and first_slope > 1e-9
        and second_slope > 1e-9
    ):
        slope_component = abs(
            math.log(
                second_slope
                / first_slope
            )
        )

    score = 1.0 - math.exp(
        -0.5
        * (
            amplitude_component
            + slope_component
        )
    )

    return round(
        float(
            np.clip(
                score,
                0.0,
                1.0,
            )
        ),
        4,
    )


def _lead_agreement(
    leads: Mapping[str, np.ndarray],
) -> float | None:
    prepared: list[np.ndarray] = []

    for values in leads.values():
        signal = np.asarray(
            values,
            dtype=np.float64,
        )
        signal = signal[
            np.isfinite(signal)
        ]

        if signal.size < 16:
            continue

        signal = signal - np.mean(signal)
        scale = float(
            np.std(signal)
        )

        if scale <= 1e-9:
            continue

        prepared.append(
            signal / scale
        )

    correlations: list[float] = []

    for index, first in enumerate(
        prepared
    ):
        for second in prepared[
            index + 1:
        ]:
            length = min(
                first.size,
                second.size,
            )

            if length < 16:
                continue

            correlation = float(
                np.corrcoef(
                    first[:length],
                    second[:length],
                )[0, 1]
            )

            if math.isfinite(
                correlation
            ):
                correlations.append(
                    abs(correlation)
                )

    if not correlations:
        return None

    return round(
        100.0
        * float(
            np.median(
                correlations
            )
        ),
        2,
    )


def _slice(
    values: np.ndarray,
    window: Mapping[str, Any],
) -> np.ndarray:
    return np.asarray(
        values,
        dtype=np.float64,
    )[
        int(window["startSample"]):
        int(window["endSample"])
    ]


def _diagnosis_text(
    metadata: Mapping[str, Any],
) -> str:
    parts = [
        metadata.get("display"),
        metadata.get("label"),
        (
            metadata.get(
                "referenceFinding"
            )
            or {}
        ).get("display"),
        (
            metadata.get(
                "evaluationScenario"
            )
            or {}
        ).get(
            "episode",
            {},
        ).get("display"),
    ]

    return " ".join(
        str(item or "")
        for item in parts
    ).lower()


def build_windowed_phase6_analysis(
    *,
    waveforms_mv: Mapping[
        str,
        np.ndarray,
    ],
    metadata: Mapping[str, Any],
    sample_rate_hz: float,
    lead_ids: list[str] | None = None,
) -> dict[str, Any]:
    sample_rate = float(
        sample_rate_hz
    )

    if sample_rate <= 0:
        raise ValueError(
            "sample_rate_hz must be positive."
        )

    usable = {
        str(lead_id): np.asarray(
            values,
            dtype=np.float64,
        )
        for lead_id, values
        in waveforms_mv.items()
        if np.asarray(values).ndim == 1
    }

    if not usable:
        raise ValueError(
            "No one-dimensional waveform leads were supplied."
        )

    total_samples = min(
        values.size
        for values
        in usable.values()
    )

    windows = measurement_windows(
        metadata,
        sample_rate_hz=sample_rate,
        total_samples=total_samples,
    )

    preferred_leads = [
        "lead2",
        "II",
        "lead1",
        "I",
        *(
            lead_ids
            or []
        ),
    ]
    analysis_lead = next(
        (
            lead_id
            for lead_id
            in preferred_leads
            if lead_id in usable
        ),
        next(iter(usable)),
    )

    rate_results: dict[
        str,
        dict[str, Any],
    ] = {}
    qrs_results: dict[
        str,
        dict[str, Any],
    ] = {}

    for window_name, window in windows.items():
        segment = _slice(
            usable[analysis_lead],
            window,
        )
        rate_result = _rate_measurement(
            segment,
            sample_rate,
        )
        qrs_result = _qrs_measurement(
            segment,
            sample_rate,
            rate_result,
        )
        rate_results[
            window_name
        ] = rate_result
        qrs_results[
            window_name
        ] = qrs_result

    event_rate = rate_results[
        "controlledEvent"
    ]
    diagnosis_text = _diagnosis_text(
        metadata
    )

    if any(
        marker in diagnosis_text
        for marker
        in _EVENT_HR_INVALID_DIAGNOSIS_MARKERS
    ):
        event_rate = {
            **event_rate,
            "medianBpm": None,
            "rangeBpm": None,
            "measurementValid": False,
            "confidenceGrade": "insufficient",
            "reason": (
                "No reliable organized beat "
                "sequence is clinically meaningful "
                "for the controlled ventricular-"
                "fibrillation window."
            ),
        }
        rate_results[
            "controlledEvent"
        ] = event_rate

    event_qrs = qrs_results[
        "controlledEvent"
    ]

    if not event_rate.get(
        "measurementValid"
    ):
        event_qrs = {
            **event_qrs,
            "medianMs": None,
            "rangeMs": None,
            "measurementValid": False,
            "confidenceGrade": "insufficient",
            "reason": (
                "QRS duration requires a reliable "
                "organized beat sequence in the "
                "controlled-event window."
            ),
        }
        qrs_results[
            "controlledEvent"
        ] = event_qrs

    pre_lead = _slice(
        usable[analysis_lead],
        windows["preEvent"],
    )
    event_lead = _slice(
        usable[analysis_lead],
        windows["controlledEvent"],
    )
    post_lead = _slice(
        usable[analysis_lead],
        windows["postEvent"],
    )
    event_leads = {
        lead_id: _slice(
            values,
            windows[
                "controlledEvent"
            ],
        )
        for lead_id, values
        in usable.items()
    }

    morphology = {
        "eventVsPreDifferenceScore":
            _difference_score(
                pre_lead,
                event_lead,
            ),
        "eventVsPostDifferenceScore":
            _difference_score(
                post_lead,
                event_lead,
            ),
        "leadAgreementScore":
            _lead_agreement(
                event_leads
            ),
        "measurementWindow":
            "controlled_event",
    }

    reasons: list[str] = []

    event_grade = str(
        event_rate.get(
            "confidenceGrade"
        )
        or "insufficient"
    )

    qrs_grade = str(
        event_qrs.get(
            "confidenceGrade"
        )
        or "insufficient"
    )

    grade_values = {
        "insufficient": 0,
        "low": 1,
        "moderate": 2,
        "high": 3,
    }
    combined_value = min(
        grade_values.get(
            event_grade,
            0,
        ),
        grade_values.get(
            qrs_grade,
            0,
        ),
    )

    if (
        not event_rate.get(
            "measurementValid"
        )
    ):
        reasons.append(
            str(
                event_rate.get(
                    "reason"
                )
                or (
                    "Event-window heart rate "
                    "was not reliable."
                )
            )
        )

    if (
        not event_qrs.get(
            "measurementValid"
        )
    ):
        reasons.append(
            str(
                event_qrs.get(
                    "reason"
                )
                or (
                    "Event-window QRS duration "
                    "was not reliable."
                )
            )
        )

    confidence_grade = {
        0: "insufficient",
        1: "low",
        2: "moderate",
        3: "high",
    }[
        combined_value
    ]

    confidence_score = {
        "insufficient": 0.0,
        "low": 35.0,
        "moderate": 70.0,
        "high": 90.0,
    }[
        confidence_grade
    ]

    limitations: list[str] = []

    for result in (
        event_rate,
        event_qrs,
    ):
        reason = str(
            result.get(
                "reason"
            )
            or ""
        ).strip()

        if (
            reason
            and reason
            not in limitations
        ):
            limitations.append(
                reason
            )

    limitations.extend(
        [
            (
                "Windowed Phase 6 values are "
                "independent measurements only; "
                "they do not diagnose or confirm "
                "the controlled rhythm."
            ),
            (
                "Only controlled-event-window "
                "measurements may be compared with "
                "controlled episode-package values."
            ),
        ]
    )

    full_rate = rate_results[
        "fullCapture"
    ]
    pre_rate = rate_results[
        "preEvent"
    ]
    post_rate = rate_results[
        "postEvent"
    ]

    full_qrs = qrs_results[
        "fullCapture"
    ]
    pre_qrs = qrs_results[
        "preEvent"
    ]
    post_qrs = qrs_results[
        "postEvent"
    ]

    return {
        "schemaVersion":
            WINDOWED_SCHEMA_VERSION,
        "analysisStatus": (
            "complete"
            if windows[
                "controlledEvent"
            ]["sampleCount"] > 0
            else "partial"
        ),
        "sampleRateHz": round(
            sample_rate,
            3,
        ),
        "analysisLead": analysis_lead,
        "measurementWindows": windows,
        "heartRate": {
            "fullCaptureMedianBpm":
                full_rate.get(
                    "medianBpm"
                ),
            "preEventMedianBpm":
                pre_rate.get(
                    "medianBpm"
                ),
            "eventMedianBpm":
                event_rate.get(
                    "medianBpm"
                ),
            "postEventMedianBpm":
                post_rate.get(
                    "medianBpm"
                ),
            "eventRangeBpm":
                event_rate.get(
                    "rangeBpm"
                ),
            "eventMeasurementValid":
                bool(
                    event_rate.get(
                        "measurementValid"
                    )
                ),
            "eventConfidenceGrade":
                event_rate.get(
                    "confidenceGrade"
                ),
            "eventReason":
                event_rate.get(
                    "reason"
                ),
            "perWindow": rate_results,
        },
        "qrs": {
            "fullCaptureMedianMs":
                full_qrs.get(
                    "medianMs"
                ),
            "preEventMedianMs":
                pre_qrs.get(
                    "medianMs"
                ),
            "eventMedianMs":
                event_qrs.get(
                    "medianMs"
                ),
            "postEventMedianMs":
                post_qrs.get(
                    "medianMs"
                ),
            "eventRangeMs":
                event_qrs.get(
                    "rangeMs"
                ),
            "eventMeasurementValid":
                bool(
                    event_qrs.get(
                        "measurementValid"
                    )
                ),
            "eventConfidenceGrade":
                event_qrs.get(
                    "confidenceGrade"
                ),
            "eventReason":
                event_qrs.get(
                    "reason"
                ),
            "perWindow": qrs_results,
        },
        "morphology": morphology,
        "confidence": {
            "score": confidence_score,
            "grade": confidence_grade,
            "reasons": reasons,
        },
        "provenance": {
            "diagnosisIndependent": False,
            "triggerSource":
                metadata.get(
                    "triggerSource"
                )
                or (
                    metadata.get(
                        "provenance"
                    )
                    or {}
                ).get(
                    "annotationSource"
                ),
            "waveformSource":
                metadata.get(
                    "baseWaveformSource"
                )
                or (
                    metadata.get(
                        "provenance"
                    )
                    or {}
                ).get(
                    "waveformSource"
                )
                or (
                    "captured_episode_"
                    "waveforms"
                ),
            "measurementWindowSource":
                "saved_episode_metadata",
            "rawWaveformModified": False,
            "isIndependentDiagnosis":
                False,
        },
        "limitations": list(
            dict.fromkeys(
                limitations
            )
        ),
    }
