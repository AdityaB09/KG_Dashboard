from __future__ import annotations

from typing import Any, Mapping

import numpy as np


MIN_VALID_RR_MS = 250.0
MAX_VALID_RR_MS = 2500.0
PREMATURE_COUPLING_RATIO_MAX = 0.80
FULL_COMPENSATORY_SUM_RATIO_MIN = 0.90
FULL_COMPENSATORY_SUM_RATIO_MAX = 1.10
INCOMPLETE_COMPENSATORY_SUM_RATIO_MIN = 0.75


def _round(value: float | int | None, digits: int = 4) -> float | int | None:
    if value is None:
        return None
    numeric = float(value)
    if not np.isfinite(numeric):
        return None
    return round(numeric, digits)


def _robust_baseline_rr(
    valid_values: np.ndarray,
    valid_interval_indices: np.ndarray,
    trigger_beat_index: int | None,
) -> np.ndarray:
    mask = np.ones(valid_values.size, dtype=bool)
    if trigger_beat_index is not None:
        for original_interval_index in (trigger_beat_index - 1, trigger_beat_index):
            mask &= valid_interval_indices != original_interval_index
    baseline = valid_values[mask]
    if baseline.size < 3:
        baseline = valid_values
    if baseline.size < 3:
        return baseline

    median = float(np.median(baseline))
    mad = float(np.median(np.abs(baseline - median)))
    if mad <= np.finfo(float).eps:
        return baseline
    robust = baseline[np.abs(baseline - median) <= 3.5 * 1.4826 * mad]
    return robust if robust.size >= 3 else baseline


def analyze_rr_metrics(
    r_peak_analysis: Mapping[str, Any],
    sampling_rate_hz: float,
    trigger_beat_index: int | None = None,
) -> dict[str, Any]:
    sampling_rate_hz = float(sampling_rate_hz)
    peaks = np.asarray(r_peak_analysis.get("rPeakSamples") or [], dtype=np.int64)
    if trigger_beat_index is None:
        raw_index = r_peak_analysis.get("triggerBeatIndex")
        trigger_beat_index = int(raw_index) if raw_index is not None else None

    if peaks.size < 3:
        return {
            "status": "failed",
            "failureReason": "fewer_than_three_r_peaks",
            "allRrIntervalsSamples": [],
            "allRrIntervalsMilliseconds": [],
            "validatedRrIntervalsSamples": [],
            "validatedRrIntervalsMilliseconds": [],
            "excludedRrIntervals": [],
            "validIntervalCount": 0,
            "excludedIntervalCount": 0,
            "excludedIntervalPercent": 0.0,
            "confidence": 0.0,
        }

    all_samples = np.diff(peaks)
    all_ms = all_samples / sampling_rate_hz * 1000.0
    valid_mask = (all_ms >= MIN_VALID_RR_MS) & (all_ms <= MAX_VALID_RR_MS)
    valid_indices = np.flatnonzero(valid_mask)
    valid_samples = all_samples[valid_mask]
    valid_ms = all_ms[valid_mask]

    excluded: list[dict[str, Any]] = []
    for index, (samples, milliseconds, valid) in enumerate(
        zip(all_samples.tolist(), all_ms.tolist(), valid_mask.tolist())
    ):
        if valid:
            continue
        reason = (
            "below_supported_minimum_rr"
            if milliseconds < MIN_VALID_RR_MS
            else "above_supported_maximum_rr"
        )
        excluded.append(
            {
                "intervalIndex": index,
                "startBeatIndex": index,
                "endBeatIndex": index + 1,
                "rrSamples": int(samples),
                "rrMilliseconds": _round(milliseconds, 3),
                "reason": reason,
            }
        )

    excluded_percent = 100.0 * len(excluded) / max(1, all_ms.size)
    if valid_ms.size < 2:
        return {
            "status": "failed",
            "failureReason": "fewer_than_two_validated_rr_intervals",
            "allRrIntervalsSamples": all_samples.astype(int).tolist(),
            "allRrIntervalsMilliseconds": [_round(item, 4) for item in all_ms],
            "validatedRrIntervalsSamples": valid_samples.astype(int).tolist(),
            "validatedRrIntervalsMilliseconds": [_round(item, 4) for item in valid_ms],
            "rrIntervalsSamples": valid_samples.astype(int).tolist(),
            "rrIntervalsMilliseconds": [_round(item, 4) for item in valid_ms],
            "excludedRrIntervals": excluded,
            "validIntervalCount": int(valid_ms.size),
            "excludedIntervalCount": len(excluded),
            "excludedIntervalPercent": _round(excluded_percent, 3),
            "confidence": 0.0,
        }

    instantaneous_hr = 60000.0 / valid_ms
    baseline_values = _robust_baseline_rr(valid_ms, valid_indices, trigger_beat_index)
    baseline_median = float(np.median(baseline_values)) if baseline_values.size else None

    coupling_ms = None
    post_pause_ms = None
    coupling_ratio = None
    post_pause_ratio = None
    compensatory_sum_ratio = None
    premature = False
    compensatory_status = "indeterminate"

    if trigger_beat_index is not None and 0 < trigger_beat_index < peaks.size - 1:
        coupling_original_index = trigger_beat_index - 1
        pause_original_index = trigger_beat_index
        if valid_mask[coupling_original_index]:
            coupling_ms = float(all_ms[coupling_original_index])
        if valid_mask[pause_original_index]:
            post_pause_ms = float(all_ms[pause_original_index])

        if baseline_median and coupling_ms is not None:
            coupling_ratio = coupling_ms / baseline_median
            premature = coupling_ratio <= PREMATURE_COUPLING_RATIO_MAX
        if baseline_median and post_pause_ms is not None:
            post_pause_ratio = post_pause_ms / baseline_median

        if not premature:
            compensatory_status = "not_applicable"
        elif baseline_median and coupling_ms is not None and post_pause_ms is not None:
            compensatory_sum_ratio = (coupling_ms + post_pause_ms) / (2.0 * baseline_median)
            if (
                FULL_COMPENSATORY_SUM_RATIO_MIN
                <= compensatory_sum_ratio
                <= FULL_COMPENSATORY_SUM_RATIO_MAX
                and post_pause_ms > baseline_median
            ):
                compensatory_status = "full"
            elif (
                compensatory_sum_ratio >= INCOMPLETE_COMPENSATORY_SUM_RATIO_MIN
                and post_pause_ms > baseline_median
            ):
                compensatory_status = "incomplete"
            else:
                compensatory_status = "not_supported"
        else:
            compensatory_status = "indeterminate"

    median_rr = float(np.median(valid_ms))
    mean_rr = float(np.mean(valid_ms))
    std_rr = float(np.std(valid_ms))
    q25, q75 = np.percentile(valid_ms, [25, 75])
    robust_variability = float(q75 - q25)
    coefficient_of_variation = std_rr / max(mean_rr, np.finfo(float).eps)
    if coefficient_of_variation <= 0.08:
        rhythm_regularity = "regular"
    elif coefficient_of_variation <= 0.18:
        rhythm_regularity = "mildly_irregular"
    else:
        rhythm_regularity = "irregular"

    r_peak_status = str(r_peak_analysis.get("status") or "ready")
    status = "ready"
    limitations: list[str] = []
    if excluded_percent > 10.0 or r_peak_status != "ready":
        status = "partial"
    if excluded:
        limitations.append(
            f"{len(excluded)} RR interval(s) were excluded from heart-rate statistics because they were outside {MIN_VALID_RR_MS:.0f}-{MAX_VALID_RR_MS:.0f} ms."
        )
    metadata_validation = r_peak_analysis.get("validation") or {}
    if "calculated_hr_differs_materially_from_metadata_hr" in (
        metadata_validation.get("reasons") or []
    ):
        status = "partial"
        limitations.append(
            "Calculated median heart rate differs materially from the stored trigger-heart-rate metadata; metadata was used only for validation, not peak selection."
        )

    valid_fraction = valid_ms.size / max(1, all_ms.size)
    confidence = 100.0 * valid_fraction
    if status == "partial":
        confidence = min(confidence, 75.0)

    return {
        "status": status,
        "allRrIntervalsSamples": all_samples.astype(int).tolist(),
        "allRrIntervalsMilliseconds": [_round(item, 4) for item in all_ms],
        "validatedRrIntervalsSamples": valid_samples.astype(int).tolist(),
        "validatedRrIntervalsMilliseconds": [_round(item, 4) for item in valid_ms],
        "rrIntervalsSamples": valid_samples.astype(int).tolist(),
        "rrIntervalsMilliseconds": [_round(item, 4) for item in valid_ms],
        "excludedRrIntervals": excluded,
        "validIntervalCount": int(valid_ms.size),
        "excludedIntervalCount": len(excluded),
        "excludedIntervalPercent": _round(excluded_percent, 3),
        "meanRrMilliseconds": _round(mean_rr, 4),
        "medianRrMilliseconds": _round(median_rr, 4),
        "minimumRrMilliseconds": _round(float(np.min(valid_ms)), 4),
        "maximumRrMilliseconds": _round(float(np.max(valid_ms)), 4),
        "rrStandardDeviationMilliseconds": _round(std_rr, 4),
        "robustRrVariabilityMilliseconds": _round(robust_variability, 4),
        "instantaneousHeartRatesBpm": [_round(item, 4) for item in instantaneous_hr],
        "meanHeartRateBpm": _round(float(np.mean(instantaneous_hr)), 4),
        "medianHeartRateBpm": _round(float(np.median(instantaneous_hr)), 4),
        "minimumHeartRateBpm": _round(float(np.min(instantaneous_hr)), 4),
        "maximumHeartRateBpm": _round(float(np.max(instantaneous_hr)), 4),
        "rhythmRegularity": rhythm_regularity,
        "baselineMedianRrMilliseconds": _round(baseline_median, 3),
        "triggerCouplingIntervalMilliseconds": _round(coupling_ms, 3),
        "couplingRatio": _round(coupling_ratio, 4),
        "prematureTimingEvidence": bool(premature),
        "postTriggerPauseMilliseconds": _round(post_pause_ms, 3),
        "postTriggerPauseRatio": _round(post_pause_ratio, 4),
        "compensatorySumRatio": _round(compensatory_sum_ratio, 4),
        "compensatoryPauseStatus": compensatory_status,
        "metadataHeartRateComparison": {
            "metadataHeartRateBpm": metadata_validation.get("metadataHeartRateBpm"),
            "calculatedMedianHeartRateBpm": _round(float(np.median(instantaneous_hr)), 3),
            "differenceFraction": metadata_validation.get("metadataHeartRateDifferenceFraction"),
            "metadataUsedForDetection": False,
        },
        "confidence": _round(confidence, 2),
        "limitations": limitations,
    }