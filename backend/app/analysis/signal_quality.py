from __future__ import annotations

from typing import Any

import numpy as np
from scipy.signal import (
    butter,
    sosfiltfilt,
)

from app.analysis.constants import (
    BASELINE_CUTOFF_HZ,
    CLIPPING_EXTREMA_PERCENTILE,
    CLIPPING_MAX_PERCENT_USABLE,
    DISCONTINUITY_MIN_MV,
    DISCONTINUITY_ROBUST_Z,
    FLATLINE_DELTA_MV,
    FLATLINE_MAX_PERCENT_USABLE,
    FLATLINE_MIN_SECONDS,
    HIGH_AMPLITUDE_ROBUST_RANGE_MV,
    LOW_AMPLITUDE_ROBUST_RANGE_MV,
    MAX_BASELINE_WANDER_RMS_MV,
    MAX_DC_OFFSET_MV,
    MAX_DISCONTINUITIES_PER_MINUTE,
    MAX_HF_NOISE_RMS_MV,
    MAX_INVALID_PERCENT_USABLE,
    QUALITY_GRADE_BOUNDS,
    REPEATED_EXTREMA_RUN_SAMPLES,
    REPEATED_EXTREMA_TOLERANCE_MV,
)


def _grade(score: float) -> str:
    for lower, grade in (
        QUALITY_GRADE_BOUNDS
    ):
        if score >= lower:
            return grade

    return "poor"


def _contiguous_true_lengths(
    mask: np.ndarray,
) -> list[int]:
    if (
        mask.size == 0
        or not np.any(mask)
    ):
        return []

    padded = np.concatenate(
        (
            [False],
            mask,
            [False],
        )
    ).astype(np.int8)

    edges = np.diff(padded)

    starts = np.where(
        edges == 1
    )[0]

    ends = np.where(
        edges == -1
    )[0]

    return [
        int(end - start)
        for start, end
        in zip(starts, ends)
    ]


def _safe_lowpass(
    signal: np.ndarray,
    sampling_rate_hz: float,
    cutoff_hz: float,
) -> np.ndarray:
    if (
        signal.size < 32
        or cutoff_hz
        >= sampling_rate_hz / 2.0
    ):
        return np.zeros_like(
            signal
        )

    try:
        sos = butter(
            2,
            cutoff_hz
            / (
                sampling_rate_hz
                / 2.0
            ),
            btype="lowpass",
            output="sos",
        )

        return sosfiltfilt(
            sos,
            signal,
        )

    except ValueError:
        return np.zeros_like(
            signal
        )


def analyze_lead_quality(
    signal: np.ndarray,
    sampling_rate_hz: float,
) -> dict[str, Any]:
    sample_count = int(
        signal.size
    )

    duration_seconds = (
        sample_count
        / sampling_rate_hz
    )

    finite_mask = np.isfinite(
        signal
    )

    finite = signal[finite_mask]

    invalid_count = int(
        (~finite_mask).sum()
    )

    invalid_percent = (
        100.0
        * invalid_count
        / max(sample_count, 1)
    )

    if finite.size == 0:
        return {
            "status": "failed",
            "usable": False,
            "score": 0.0,
            "grade": "poor",
            "sampleCount": (
                sample_count
            ),
            "durationSeconds": round(
                duration_seconds,
                4,
            ),
            "missingSamplePercent": round(
                invalid_percent,
                6,
            ),
            "limitations": [
                (
                    "No finite samples "
                    "are available."
                )
            ],
        }

    working = signal.copy()

    if invalid_count:
        indices = np.arange(
            sample_count
        )

        working[~finite_mask] = (
            np.interp(
                indices[~finite_mask],
                indices[finite_mask],
                finite,
            )
        )

    differences = np.diff(
        working
    )

    flat_mask = (
        np.abs(differences)
        <= FLATLINE_DELTA_MV
    )

    minimum_flat_samples = max(
        1,
        int(
            round(
                FLATLINE_MIN_SECONDS
                * sampling_rate_hz
            )
        ),
    )

    flat_lengths = [
        length
        for length
        in _contiguous_true_lengths(
            flat_mask
        )
        if length
        >= minimum_flat_samples
    ]

    flat_samples = int(
        sum(
            length + 1
            for length
            in flat_lengths
        )
    )

    flatline_percent = (
        100.0
        * min(
            flat_samples,
            sample_count,
        )
        / max(sample_count, 1)
    )

    longest_flatline_seconds = (
        (
            max(flat_lengths) + 1
        )
        / sampling_rate_hz
        if flat_lengths
        else 0.0
    )

    low_value = float(
        np.percentile(
            working,
            100.0
            - CLIPPING_EXTREMA_PERCENTILE,
        )
    )

    high_value = float(
        np.percentile(
            working,
            CLIPPING_EXTREMA_PERCENTILE,
        )
    )

    near_low = (
        np.abs(
            working
            - np.min(working)
        )
        <= REPEATED_EXTREMA_TOLERANCE_MV
    )

    near_high = (
        np.abs(
            working
            - np.max(working)
        )
        <= REPEATED_EXTREMA_TOLERANCE_MV
    )

    extrema_runs = [
        length
        for length
        in _contiguous_true_lengths(
            near_low | near_high
        )
        if length
        >= REPEATED_EXTREMA_RUN_SAMPLES
    ]

    repeated_extrema_samples = int(
        sum(extrema_runs)
    )

    clipping_percent = (
        100.0
        * repeated_extrema_samples
        / max(sample_count, 1)
    )

    saturation_detected = bool(
        extrema_runs
        and (
            high_value
            - low_value
        )
        > 0
    )

    median_difference = (
        float(
            np.median(
                differences
            )
        )
        if differences.size
        else 0.0
    )

    mad_difference = (
        float(
            np.median(
                np.abs(
                    differences
                    - median_difference
                )
            )
        )
        if differences.size
        else 0.0
    )

    robust_sigma = max(
        1.4826 * mad_difference,
        1e-6,
    )

    discontinuity_threshold = max(
        DISCONTINUITY_MIN_MV,
        DISCONTINUITY_ROBUST_Z
        * robust_sigma,
    )

    discontinuities = np.where(
        np.abs(
            differences
            - median_difference
        )
        > discontinuity_threshold
    )[0]

    discontinuity_count = int(
        discontinuities.size
    )

    discontinuities_per_minute = (
        discontinuity_count
        / max(
            duration_seconds / 60.0,
            1e-9,
        )
    )

    dc_offset_mv = float(
        np.median(working)
    )

    p1, p5, p95, p99 = (
        np.percentile(
            working,
            [1, 5, 95, 99],
        )
    )

    robust_range_mv = float(
        p95 - p5
    )

    signal_range_mv = float(
        np.max(working)
        - np.min(working)
    )

    baseline = _safe_lowpass(
        working - dc_offset_mv,
        sampling_rate_hz,
        BASELINE_CUTOFF_HZ,
    )

    baseline_wander_rms_mv = float(
        np.sqrt(
            np.mean(
                np.square(baseline)
            )
        )
    )

    qrs_band = (
        working
        - baseline
        - dc_offset_mv
    )

    smooth = _safe_lowpass(
        qrs_band,
        sampling_rate_hz,
        min(
            35.0,
            sampling_rate_hz * 0.20,
        ),
    )

    high_frequency = (
        qrs_band - smooth
    )

    hf_noise_rms_mv = float(
        np.sqrt(
            np.mean(
                np.square(
                    high_frequency
                )
            )
        )
    )

    low_amplitude = (
        robust_range_mv
        < LOW_AMPLITUDE_ROBUST_RANGE_MV
    )

    implausibly_high_amplitude = (
        robust_range_mv
        > HIGH_AMPLITUDE_ROBUST_RANGE_MV
    )

    penalties = {
        "invalidSamples": min(
            35.0,
            invalid_percent * 4.0,
        ),
        "flatline": min(
            35.0,
            flatline_percent * 1.8,
        ),
        "clipping": min(
            25.0,
            clipping_percent * 3.0,
        ),
        "discontinuities": min(
            20.0,
            max(
                0.0,
                (
                    discontinuities_per_minute
                    - 2.0
                )
                * 0.9,
            ),
        ),
        "dcOffset": min(
            10.0,
            max(
                0.0,
                (
                    abs(dc_offset_mv)
                    - 0.5
                )
                * 3.0,
            ),
        ),
        "baselineWander": min(
            20.0,
            max(
                0.0,
                (
                    baseline_wander_rms_mv
                    - 0.08
                )
                * 45.0,
            ),
        ),
        "highFrequencyNoise": min(
            20.0,
            max(
                0.0,
                (
                    hf_noise_rms_mv
                    - 0.04
                )
                * 70.0,
            ),
        ),
        "lowAmplitude": (
            25.0
            if low_amplitude
            else 0.0
        ),
        "highAmplitude": (
            25.0
            if implausibly_high_amplitude
            else 0.0
        ),
    }

    score = max(
        0.0,
        100.0
        - sum(
            penalties.values()
        ),
    )

    limitations: list[str] = []

    if invalid_percent > 0:
        limitations.append(
            (
                f"{invalid_percent:.3f}% "
                "non-finite samples "
                "were detected."
            )
        )

    if (
        flatline_percent
        > FLATLINE_MAX_PERCENT_USABLE
    ):
        limitations.append(
            (
                "Extended flatline-like "
                "segments reduce lead "
                "usability."
            )
        )

    if (
        clipping_percent
        > CLIPPING_MAX_PERCENT_USABLE
    ):
        limitations.append(
            (
                "Repeated extrema suggest "
                "clipping or saturation."
            )
        )

    if (
        discontinuities_per_minute
        > MAX_DISCONTINUITIES_PER_MINUTE
    ):
        limitations.append(
            (
                "Frequent sudden "
                "discontinuities were "
                "detected."
            )
        )

    if (
        abs(dc_offset_mv)
        > MAX_DC_OFFSET_MV
    ):
        limitations.append(
            "Large DC offset was detected."
        )

    if (
        baseline_wander_rms_mv
        > MAX_BASELINE_WANDER_RMS_MV
    ):
        limitations.append(
            "Baseline wander is high."
        )

    if (
        hf_noise_rms_mv
        > MAX_HF_NOISE_RMS_MV
    ):
        limitations.append(
            (
                "High-frequency noise "
                "is high."
            )
        )

    if low_amplitude:
        limitations.append(
            (
                "Robust amplitude is too "
                "low for reliable "
                "morphology measurement."
            )
        )

    if implausibly_high_amplitude:
        limitations.append(
            (
                "Robust amplitude is "
                "implausibly high for "
                "ECG in millivolts."
            )
        )

    usable = bool(
        score >= 45.0
        and invalid_percent
        <= MAX_INVALID_PERCENT_USABLE
        and flatline_percent <= 40.0
        and not (
            implausibly_high_amplitude
        )
        and finite.size
        >= max(
            20,
            int(sampling_rate_hz),
        )
    )

    return {
        "status": "ready",
        "usable": usable,
        "score": round(score, 2),
        "grade": _grade(score),
        "sampleCount": sample_count,
        "durationSeconds": round(
            duration_seconds,
            4,
        ),
        "missingSamplePercent": round(
            invalid_percent,
            6,
        ),
        "flatlineDetected": bool(
            flat_lengths
        ),
        "flatlineDurationSeconds": round(
            sum(flat_lengths)
            / sampling_rate_hz,
            4,
        ),
        "longestFlatlineSeconds": round(
            longest_flatline_seconds,
            4,
        ),
        "flatlinePercent": round(
            flatline_percent,
            4,
        ),
        "clippingDetected": bool(
            extrema_runs
        ),
        "saturationDetected": (
            saturation_detected
        ),
        "repeatedExtremaRunCount": (
            len(extrema_runs)
        ),
        "clippingPercent": round(
            clipping_percent,
            4,
        ),
        "discontinuityDetected": (
            discontinuity_count > 0
        ),
        "discontinuityCount": (
            discontinuity_count
        ),
        "discontinuitiesPerMinute": round(
            discontinuities_per_minute,
            3,
        ),
        "discontinuityThresholdMv": round(
            discontinuity_threshold,
            6,
        ),
        "dcOffsetMv": round(
            dc_offset_mv,
            6,
        ),
        "robustAmplitudeRangeMv": round(
            robust_range_mv,
            6,
        ),
        "signalRangeMv": round(
            signal_range_mv,
            6,
        ),
        "lowAmplitudeDetected": (
            low_amplitude
        ),
        "implausiblyHighAmplitudeDetected": (
            implausibly_high_amplitude
        ),
        "baselineWanderRmsMv": round(
            baseline_wander_rms_mv,
            6,
        ),
        "highFrequencyNoiseRmsMv": round(
            hf_noise_rms_mv,
            6,
        ),
        "percentilesMv": {
            "p1": round(float(p1), 6),
            "p5": round(float(p5), 6),
            "p95": round(float(p95), 6),
            "p99": round(float(p99), 6),
        },
        "penalties": {
            key: round(value, 3)
            for key, value
            in penalties.items()
        },
        "limitations": limitations,
    }


def analyze_signal_quality(
    waveforms_mv: dict[
        str,
        np.ndarray,
    ],
    sampling_rate_hz: float,
) -> dict[str, Any]:
    lead_results = {
        lead_id: analyze_lead_quality(
            signal,
            sampling_rate_hz,
        )
        for lead_id, signal
        in waveforms_mv.items()
    }

    usable_leads = [
        lead
        for lead, result
        in lead_results.items()
        if result.get("usable")
    ]

    excluded_leads = [
        lead
        for lead in waveforms_mv
        if lead not in usable_leads
    ]

    usable_scores = [
        lead_results[lead]["score"]
        for lead in usable_leads
    ]

    all_scores = [
        result.get("score", 0.0)
        for result
        in lead_results.values()
    ]

    score = float(
        np.median(
            usable_scores
            or all_scores
            or [0.0]
        )
    )

    limitations = []

    if excluded_leads:
        limitations.append(
            (
                "Excluded unusable leads: "
                f"{', '.join(excluded_leads)}."
            )
        )

    if not usable_leads:
        limitations.append(
            (
                "No lead met the minimum "
                "deterministic analysis "
                "quality criteria."
            )
        )

    sample_count = min(
        (
            signal.size
            for signal
            in waveforms_mv.values()
        ),
        default=0,
    )

    return {
        "status": "ready",
        "samplingRateHz": (
            sampling_rate_hz
        ),
        "sampleCount": int(
            sample_count
        ),
        "durationSeconds": (
            round(
                sample_count
                / sampling_rate_hz,
                4,
            )
            if sampling_rate_hz
            else 0.0
        ),
        "leadResults": lead_results,
        "overall": {
            "usable": bool(
                usable_leads
            ),
            "score": round(
                score,
                2,
            ),
            "grade": _grade(score),
            "usableLeadCount": (
                len(usable_leads)
            ),
            "excludedLeadCount": (
                len(excluded_leads)
            ),
            "usableLeadIds": (
                usable_leads
            ),
            "excludedLeadIds": (
                excluded_leads
            ),
        },
        "limitations": limitations,
    }