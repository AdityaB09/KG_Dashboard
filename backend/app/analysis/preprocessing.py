from __future__ import annotations

from typing import Any

import numpy as np
from scipy.signal import (
    butter,
    filtfilt,
    iirnotch,
    sosfiltfilt,
    welch,
)

from app.analysis.constants import (
    BANDPASS_HIGH_HZ,
    BANDPASS_LOW_HZ,
    BASELINE_CUTOFF_HZ,
    FILTER_ORDER,
    NOTCH_ENABLE_MIN_SPECTRAL_RATIO,
    NOTCH_FREQUENCY_HZ,
    NOTCH_QUALITY_FACTOR,
)


def _interpolate_invalid(
    signal: np.ndarray,
) -> tuple[np.ndarray, int]:
    output = np.asarray(
        signal,
        dtype=np.float64,
    ).copy()

    finite = np.isfinite(output)
    invalid_count = int(
        (~finite).sum()
    )

    if invalid_count == 0:
        return output, 0

    if finite.sum() < 2:
        return (
            np.zeros_like(output),
            invalid_count,
        )

    indices = np.arange(
        output.size
    )

    output[~finite] = np.interp(
        indices[~finite],
        indices[finite],
        output[finite],
    )

    return output, invalid_count


def _safe_sos_filter(
    signal: np.ndarray,
    sos: np.ndarray,
) -> tuple[np.ndarray, bool]:
    try:
        minimum_length = max(
            32,
            3 * (2 * len(sos) + 1),
        )

        if signal.size < minimum_length:
            return signal.copy(), False

        return (
            sosfiltfilt(
                sos,
                signal,
            ),
            True,
        )

    except ValueError:
        return signal.copy(), False


def _line_noise_ratio(
    signal: np.ndarray,
    sampling_rate_hz: float,
    frequency_hz: float,
) -> float:
    if (
        signal.size
        < int(sampling_rate_hz * 2)
        or sampling_rate_hz
        <= frequency_hz * 2.05
    ):
        return 0.0

    frequencies, power = welch(
        signal,
        fs=sampling_rate_hz,
        nperseg=min(
            signal.size,
            int(
                sampling_rate_hz * 4
            ),
        ),
    )

    band = (
        frequencies
        >= frequency_hz - 0.8
    ) & (
        frequencies
        <= frequency_hz + 0.8
    )

    neighbors = (
        (
            frequencies
            >= frequency_hz - 5.0
        )
        & (
            frequencies
            < frequency_hz - 1.5
        )
    ) | (
        (
            frequencies
            > frequency_hz + 1.5
        )
        & (
            frequencies
            <= frequency_hz + 5.0
        )
    )

    if (
        not np.any(band)
        or not np.any(neighbors)
    ):
        return 0.0

    return float(
        np.mean(power[band])
        / max(
            np.median(
                power[neighbors]
            ),
            1e-15,
        )
    )


def preprocess_signal(
    signal: np.ndarray,
    sampling_rate_hz: float,
) -> tuple[
    np.ndarray,
    dict[str, Any],
]:
    (
        interpolated,
        invalid_count,
    ) = _interpolate_invalid(
        signal
    )

    dc_offset = (
        float(
            np.median(
                interpolated
            )
        )
        if interpolated.size
        else 0.0
    )

    centered = (
        interpolated - dc_offset
    )

    nyquist = (
        sampling_rate_hz / 2.0
    )

    baseline_removed = (
        centered.copy()
    )

    baseline_applied = False

    if (
        BASELINE_CUTOFF_HZ
        < nyquist * 0.95
    ):
        baseline_sos = butter(
            2,
            BASELINE_CUTOFF_HZ
            / nyquist,
            btype="highpass",
            output="sos",
        )

        (
            baseline_removed,
            baseline_applied,
        ) = _safe_sos_filter(
            centered,
            baseline_sos,
        )

    high_hz = min(
        BANDPASS_HIGH_HZ,
        nyquist * 0.90,
    )

    bandpassed = (
        baseline_removed.copy()
    )

    bandpass_applied = False

    if BANDPASS_LOW_HZ < high_hz:
        bandpass_sos = butter(
            FILTER_ORDER,
            [
                BANDPASS_LOW_HZ
                / nyquist,
                high_hz / nyquist,
            ],
            btype="bandpass",
            output="sos",
        )

        (
            bandpassed,
            bandpass_applied,
        ) = _safe_sos_filter(
            baseline_removed,
            bandpass_sos,
        )

    notch_ratio = _line_noise_ratio(
        bandpassed,
        sampling_rate_hz,
        NOTCH_FREQUENCY_HZ,
    )

    notch_applied = False
    filtered = bandpassed.copy()

    if (
        sampling_rate_hz
        > NOTCH_FREQUENCY_HZ * 2.05
        and notch_ratio
        >= NOTCH_ENABLE_MIN_SPECTRAL_RATIO
    ):
        try:
            b, a = iirnotch(
                NOTCH_FREQUENCY_HZ
                / nyquist,
                NOTCH_QUALITY_FACTOR,
            )

            minimum = 3 * max(
                len(a),
                len(b),
            )

            if filtered.size > minimum:
                filtered = filtfilt(
                    b,
                    a,
                    filtered,
                )

                notch_applied = True

        except ValueError:
            notch_applied = False

    details = {
        "invalidSamplesInterpolated": (
            invalid_count
        ),
        "dcOffsetRemovedMv": round(
            dc_offset,
            8,
        ),
        "baselineRemoval": {
            "type": (
                "zero_phase_"
                "butterworth_highpass"
            ),
            "cutoffHz": (
                BASELINE_CUTOFF_HZ
            ),
            "applied": (
                baseline_applied
            ),
        },
        "bandpass": {
            "type": (
                "zero_phase_"
                "butterworth_sos"
            ),
            "order": FILTER_ORDER,
            "lowHz": (
                BANDPASS_LOW_HZ
            ),
            "highHz": round(
                high_hz,
                4,
            ),
            "applied": (
                bandpass_applied
            ),
            "qrsSafe": True,
        },
        "notch": {
            "frequencyHz": (
                NOTCH_FREQUENCY_HZ
            ),
            "qualityFactor": (
                NOTCH_QUALITY_FACTOR
            ),
            "spectralRatio": round(
                notch_ratio,
                4,
            ),
            "justificationThreshold": (
                NOTCH_ENABLE_MIN_SPECTRAL_RATIO
            ),
            "applied": notch_applied,
        },
        "shortSignalFallbackUsed": (
            not (
                baseline_applied
                and bandpass_applied
            )
        ),
        "rawWaveformModified": False,
    }

    return (
        np.asarray(
            filtered,
            dtype=np.float64,
        ),
        details,
    )


def preprocess_leads(
    waveforms_mv: dict[
        str,
        np.ndarray,
    ],
    sampling_rate_hz: float,
    usable_leads: list[str],
) -> tuple[
    dict[str, np.ndarray],
    dict[str, Any],
]:
    output: dict[
        str,
        np.ndarray,
    ] = {}

    per_lead: dict[
        str,
        Any,
    ] = {}

    for lead_id in usable_leads:
        (
            filtered,
            details,
        ) = preprocess_signal(
            waveforms_mv[lead_id],
            sampling_rate_hz,
        )

        output[lead_id] = filtered
        per_lead[lead_id] = details

    return output, {
        "status": (
            "ready"
            if output
            else "failed"
        ),
        "samplingRateHz": (
            sampling_rate_hz
        ),
        "processedLeadIds": sorted(
            output
        ),
        "excludedLeadIds": sorted(
            set(waveforms_mv)
            - set(output)
        ),
        "filterConfiguration": {
            "dcOffsetRemoval": (
                "median subtraction"
            ),
            "baselineCutoffHz": (
                BASELINE_CUTOFF_HZ
            ),
            "bandpassLowHz": (
                BANDPASS_LOW_HZ
            ),
            "bandpassHighHz": (
                BANDPASS_HIGH_HZ
            ),
            "notchFrequencyHz": (
                NOTCH_FREQUENCY_HZ
            ),
            "notchConditional": True,
        },
        "leadResults": per_lead,
        "rawWaveformModified": False,
        "provenance": (
            "measurement_copy_only"
        ),
    }