from __future__ import annotations

from collections import defaultdict
from math import ceil
from typing import Any, Mapping

import numpy as np
from scipy.signal import butter, find_peaks, sosfiltfilt


MIN_RR_SECONDS = 0.25
PER_LEAD_REFRACTORY_SECONDS = 0.28
CONSENSUS_TOLERANCE_SECONDS = 0.05
QRS_BAND_LOW_HZ = 5.0
QRS_BAND_HIGH_HZ = 20.0
INTEGRATION_WINDOW_SECONDS = 0.12
REFINE_WINDOW_SECONDS = 0.08
MAX_SUPPORTED_HEART_RATE_BPM = 240.0
METADATA_HR_WARNING_FRACTION = 0.35


def _round(value: float | int | None, digits: int = 4) -> float | int | None:
    if value is None:
        return None
    numeric = float(value)
    if not np.isfinite(numeric):
        return None
    return round(numeric, digits)


def _as_lead_mapping(value: Any) -> dict[str, np.ndarray]:
    """Accept a direct mapping or the common preprocessing runtime containers."""
    if hasattr(value, "filtered_leads"):
        value = getattr(value, "filtered_leads")
    elif hasattr(value, "measurement_leads"):
        value = getattr(value, "measurement_leads")

    if isinstance(value, Mapping):
        for key in (
            "filteredLeads",
            "filtered_leads",
            "measurementLeads",
            "measurement_leads",
            "leads",
        ):
            nested = value.get(key)
            if isinstance(nested, Mapping):
                value = nested
                break

    if not isinstance(value, Mapping):
        raise TypeError("R-peak analysis requires a mapping of lead ID to waveform samples.")

    result: dict[str, np.ndarray] = {}
    for lead_id, samples in value.items():
        array = np.asarray(samples, dtype=np.float64).reshape(-1)
        if array.size:
            result[str(lead_id)] = array
    return result


def _usable_lead_ids(
    signal_quality: Mapping[str, Any] | None,
    available_lead_ids: list[str],
) -> list[str]:
    if not signal_quality:
        return available_lead_ids

    overall = signal_quality.get("overall", {})
    explicit = overall.get("usableLeadIds")
    if isinstance(explicit, list) and explicit:
        return [lead_id for lead_id in explicit if lead_id in available_lead_ids]

    excluded = set(overall.get("excludedLeadIds") or [])
    lead_results = signal_quality.get("leadResults") or {}
    usable: list[str] = []
    for lead_id in available_lead_ids:
        lead_result = lead_results.get(lead_id, {})
        if lead_id in excluded:
            continue
        if lead_result and lead_result.get("usable") is False:
            continue
        usable.append(lead_id)
    return usable


def _safe_zero_phase_bandpass(
    samples: np.ndarray,
    sampling_rate_hz: float,
) -> np.ndarray:
    centered = samples - np.nanmedian(samples)
    nyquist = sampling_rate_hz / 2.0
    low = max(0.5, min(QRS_BAND_LOW_HZ, nyquist * 0.25))
    high = min(QRS_BAND_HIGH_HZ, nyquist * 0.85)
    if high <= low or centered.size < max(24, int(sampling_rate_hz)):
        return centered

    sos = butter(3, [low / nyquist, high / nyquist], btype="bandpass", output="sos")
    try:
        return sosfiltfilt(sos, centered)
    except ValueError:
        return centered


def _moving_average(values: np.ndarray, window: int) -> np.ndarray:
    window = max(1, int(window))
    if window == 1:
        return values.copy()
    kernel = np.ones(window, dtype=np.float64) / float(window)
    return np.convolve(values, kernel, mode="same")


def _suppress_close_candidates(
    samples: list[int],
    strengths: list[float],
    minimum_distance_samples: int,
) -> tuple[list[int], list[float]]:
    if not samples:
        return [], []

    selected: list[tuple[int, float]] = []
    for sample, strength in sorted(zip(samples, strengths), key=lambda item: item[0]):
        if not selected or sample - selected[-1][0] >= minimum_distance_samples:
            selected.append((sample, strength))
            continue
        if strength > selected[-1][1]:
            selected[-1] = (sample, strength)

    return [item[0] for item in selected], [item[1] for item in selected]


def _detect_lead_candidates(
    samples: np.ndarray,
    sampling_rate_hz: float,
) -> dict[str, Any]:
    qrs_signal = _safe_zero_phase_bandpass(samples, sampling_rate_hz)
    derivative = np.diff(qrs_signal, prepend=qrs_signal[0])
    energy = derivative * derivative
    integrated = _moving_average(
        energy,
        max(3, round(INTEGRATION_WINDOW_SECONDS * sampling_rate_hz)),
    )

    finite = integrated[np.isfinite(integrated)]
    if finite.size < 8 or float(np.max(finite)) <= 0.0:
        return {
            "candidateSamples": [],
            "candidateStrengths": [],
            "prominenceThreshold": 0.0,
            "medianProminence": 0.0,
            "qrsSignal": qrs_signal,
        }

    p50 = float(np.percentile(finite, 50))
    p90 = float(np.percentile(finite, 90))
    prominence_threshold = max(np.finfo(float).eps, (p90 - p50) * 0.20)
    height_threshold = p50 + (p90 - p50) * 0.18
    per_lead_distance = max(1, round(PER_LEAD_REFRACTORY_SECONDS * sampling_rate_hz))

    envelope_peaks, properties = find_peaks(
        integrated,
        distance=per_lead_distance,
        prominence=prominence_threshold,
        height=height_threshold,
    )

    refine_radius = max(1, round(REFINE_WINDOW_SECONDS * sampling_rate_hz))
    refined_samples: list[int] = []
    refined_strengths: list[float] = []
    prominences = properties.get("prominences", np.zeros(envelope_peaks.size))

    for peak, prominence in zip(envelope_peaks.tolist(), prominences.tolist()):
        start = max(0, int(peak) - refine_radius)
        stop = min(qrs_signal.size, int(peak) + refine_radius + 1)
        local = np.abs(qrs_signal[start:stop])
        if not local.size:
            continue
        refined = start + int(np.argmax(local))
        strength = float(abs(qrs_signal[refined])) + float(np.sqrt(max(prominence, 0.0)))
        refined_samples.append(refined)
        refined_strengths.append(strength)

    refined_samples, refined_strengths = _suppress_close_candidates(
        refined_samples,
        refined_strengths,
        per_lead_distance,
    )

    return {
        "candidateSamples": refined_samples,
        "candidateStrengths": refined_strengths,
        "prominenceThreshold": prominence_threshold,
        "medianProminence": float(np.median(prominences)) if len(prominences) else 0.0,
        "qrsSignal": qrs_signal,
    }


def _cluster_candidates(
    per_lead_internal: Mapping[str, dict[str, Any]],
    sampling_rate_hz: float,
) -> list[dict[str, Any]]:
    tolerance = max(1, round(CONSENSUS_TOLERANCE_SECONDS * sampling_rate_hz))
    events: list[tuple[int, str, float]] = []
    for lead_id, result in per_lead_internal.items():
        for sample, strength in zip(
            result.get("candidateSamples", []),
            result.get("candidateStrengths", []),
        ):
            events.append((int(sample), lead_id, float(strength)))
    events.sort(key=lambda item: item[0])

    clusters: list[list[tuple[int, str, float]]] = []
    for event in events:
        if not clusters:
            clusters.append([event])
            continue
        center = int(round(np.median([item[0] for item in clusters[-1]])))
        if event[0] - center <= tolerance:
            clusters[-1].append(event)
        else:
            clusters.append([event])

    results: list[dict[str, Any]] = []
    for cluster in clusters:
        strongest_by_lead: dict[str, tuple[int, str, float]] = {}
        for event in cluster:
            previous = strongest_by_lead.get(event[1])
            if previous is None or event[2] > previous[2]:
                strongest_by_lead[event[1]] = event
        unique_events = list(strongest_by_lead.values())
        samples = np.asarray([item[0] for item in unique_events], dtype=np.float64)
        strengths = np.asarray([max(item[2], np.finfo(float).eps) for item in unique_events])
        weighted_sample = int(round(float(np.average(samples, weights=strengths))))
        results.append(
            {
                "sample": weighted_sample,
                "leadCount": len(unique_events),
                "leadIds": sorted(strongest_by_lead),
                "spreadSamples": int(max(samples) - min(samples)) if samples.size else 0,
                "medianStrength": float(np.median(strengths)) if strengths.size else 0.0,
                "quality": float(len(unique_events) * 10.0 + np.median(strengths)),
            }
        )
    return results


def _estimate_dominant_rr_samples(
    clusters: list[dict[str, Any]],
    sampling_rate_hz: float,
) -> int | None:
    if len(clusters) < 4:
        return None
    samples = np.asarray([item["sample"] for item in clusters], dtype=np.int64)
    intervals = np.diff(samples)
    lower = round(0.36 * sampling_rate_hz)
    upper = round(1.50 * sampling_rate_hz)
    plausible = intervals[(intervals >= lower) & (intervals <= upper)]
    if plausible.size < 3:
        return None

    bin_width = max(1, round(0.04 * sampling_rate_hz))
    bins = np.round(plausible / bin_width).astype(int)
    values, counts = np.unique(bins, return_counts=True)
    winning_bin = int(values[int(np.argmax(counts))])
    members = plausible[np.abs(bins - winning_bin) <= 1]
    return int(round(float(np.median(members)))) if members.size else None


def _global_nonmaximum_suppression(
    clusters: list[dict[str, Any]],
    minimum_distance_samples: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []

    for candidate in sorted(clusters, key=lambda item: item["quality"], reverse=True):
        conflict = next(
            (
                existing
                for existing in selected
                if abs(int(candidate["sample"]) - int(existing["sample"]))
                < minimum_distance_samples
            ),
            None,
        )
        if conflict is None:
            selected.append(candidate)
        else:
            suppressed.append(
                {
                    "sample": int(candidate["sample"]),
                    "reason": "global_refractory_suppression",
                    "keptSample": int(conflict["sample"]),
                    "distanceSamples": abs(int(candidate["sample"]) - int(conflict["sample"])),
                }
            )

    selected.sort(key=lambda item: item["sample"])
    suppressed.sort(key=lambda item: item["sample"])
    return selected, suppressed


def analyze_r_peaks(
    filtered_leads: Any,
    signal_quality: Mapping[str, Any] | None,
    sampling_rate_hz: float,
    dataset_annotation_sample: int | None = None,
    metadata_heart_rate_bpm: float | None = None,
) -> dict[str, Any]:
    """Detect R peaks independently, then compare the result with metadata as validation only."""
    sampling_rate_hz = float(sampling_rate_hz)
    leads = _as_lead_mapping(filtered_leads)
    available = list(leads)
    usable = _usable_lead_ids(signal_quality, available)
    minimum_consensus_leads = max(2, ceil(max(1, len(usable)) * 0.25))

    per_lead_internal: dict[str, dict[str, Any]] = {}
    per_lead_output: dict[str, dict[str, Any]] = {}
    for lead_id in usable:
        result = _detect_lead_candidates(leads[lead_id], sampling_rate_hz)
        per_lead_internal[lead_id] = result
        per_lead_output[lead_id] = {
            "candidateCount": len(result["candidateSamples"]),
            "minimumPeakDistanceSamples": round(PER_LEAD_REFRACTORY_SECONDS * sampling_rate_hz),
            "minimumPeakDistanceSeconds": PER_LEAD_REFRACTORY_SECONDS,
            "prominenceThreshold": _round(result["prominenceThreshold"], 8),
            "medianProminence": _round(result["medianProminence"], 8),
            "candidateSamples": result["candidateSamples"],
        }

    all_clusters = _cluster_candidates(per_lead_internal, sampling_rate_hz)
    consensus = [item for item in all_clusters if item["leadCount"] >= minimum_consensus_leads]
    dominant_rr = _estimate_dominant_rr_samples(consensus, sampling_rate_hz)

    hard_refractory = round(MIN_RR_SECONDS * sampling_rate_hz)
    if dominant_rr is None:
        global_refractory = max(hard_refractory, round(PER_LEAD_REFRACTORY_SECONDS * sampling_rate_hz))
    else:
        global_refractory = max(
            hard_refractory,
            min(round(0.45 * sampling_rate_hz), round(0.55 * dominant_rr)),
        )

    accepted, suppressed = _global_nonmaximum_suppression(consensus, global_refractory)
    r_peak_samples = [int(item["sample"]) for item in accepted]
    r_peak_times = [round(sample / sampling_rate_hz, 6) for sample in r_peak_samples]

    if len(r_peak_samples) < 3:
        return {
            "status": "failed",
            "failureReason": "fewer_than_three_reliable_consensus_r_peaks",
            "samplingRateHz": sampling_rate_hz,
            "perLeadResults": per_lead_output,
            "consensusClusters": accepted,
            "rPeakSamples": r_peak_samples,
            "rPeakTimesSeconds": r_peak_times,
            "detectedBeatCount": len(r_peak_samples),
            "minimumConsensusLeadCount": minimum_consensus_leads,
            "globalMinimumPeakDistanceSamples": global_refractory,
            "globalMinimumPeakDistanceSeconds": _round(global_refractory / sampling_rate_hz, 4),
            "suppressedDuplicatePeakCount": len(suppressed),
            "suppressedCandidates": suppressed,
            "validation": {
                "status": "failed",
                "reasons": ["insufficient_consensus_r_peaks"],
            },
            "confidence": 0.0,
        }

    primary_timing_lead = max(
        usable,
        key=lambda lead_id: (
            len(per_lead_internal[lead_id]["candidateSamples"]),
            float(np.median(per_lead_internal[lead_id]["candidateStrengths"]))
            if per_lead_internal[lead_id]["candidateStrengths"]
            else 0.0,
        ),
    )

    nearest_peak = None
    trigger_alignment_error_samples = None
    trigger_beat_index = None
    if dataset_annotation_sample is not None and r_peak_samples:
        trigger_beat_index = int(
            np.argmin(np.abs(np.asarray(r_peak_samples) - int(dataset_annotation_sample)))
        )
        nearest_peak = r_peak_samples[trigger_beat_index]
        trigger_alignment_error_samples = nearest_peak - int(dataset_annotation_sample)

    rr_samples = np.diff(np.asarray(r_peak_samples, dtype=np.int64))
    rr_ms = rr_samples / sampling_rate_hz * 1000.0
    instantaneous_hr = 60000.0 / rr_ms
    median_hr = float(np.median(instantaneous_hr)) if instantaneous_hr.size else None
    maximum_hr = float(np.max(instantaneous_hr)) if instantaneous_hr.size else None
    minimum_rr_ms = float(np.min(rr_ms)) if rr_ms.size else None

    median_support_fraction = float(
        np.median([item["leadCount"] / max(1, len(usable)) for item in accepted])
    )
    metadata_difference_fraction = None
    if metadata_heart_rate_bpm and median_hr:
        metadata_difference_fraction = abs(median_hr - float(metadata_heart_rate_bpm)) / max(
            float(metadata_heart_rate_bpm), 1.0
        )

    validation_reasons: list[str] = []
    if minimum_rr_ms is not None and minimum_rr_ms < MIN_RR_SECONDS * 1000.0:
        validation_reasons.append("rr_below_supported_minimum")
    if maximum_hr is not None and maximum_hr > MAX_SUPPORTED_HEART_RATE_BPM:
        validation_reasons.append("instantaneous_heart_rate_above_supported_maximum")
    if (
        metadata_difference_fraction is not None
        and metadata_difference_fraction > METADATA_HR_WARNING_FRACTION
    ):
        validation_reasons.append("calculated_hr_differs_materially_from_metadata_hr")

    validation_status = "ready" if not validation_reasons else "partial"
    alignment_score = 1.0
    if trigger_alignment_error_samples is not None:
        alignment_score = max(
            0.0,
            1.0
            - abs(trigger_alignment_error_samples)
            / max(1.0, CONSENSUS_TOLERANCE_SECONDS * sampling_rate_hz * 2.0),
        )
    confidence = 100.0 * (
        0.65 * min(1.0, median_support_fraction)
        + 0.20 * alignment_score
        + 0.15 * (1.0 if validation_status == "ready" else 0.45)
    )

    return {
        "status": validation_status,
        "samplingRateHz": sampling_rate_hz,
        "perLeadResults": per_lead_output,
        "primaryTimingLead": primary_timing_lead,
        "minimumConsensusLeadCount": minimum_consensus_leads,
        "consensusToleranceSamples": round(CONSENSUS_TOLERANCE_SECONDS * sampling_rate_hz),
        "consensusClusters": [
            {
                "sample": int(item["sample"]),
                "leadCount": int(item["leadCount"]),
                "leadIds": item["leadIds"],
                "spreadSamples": int(item["spreadSamples"]),
            }
            for item in accepted
        ],
        "dominantRrEstimateSamples": dominant_rr,
        "dominantRrEstimateMilliseconds": _round(
            dominant_rr / sampling_rate_hz * 1000.0 if dominant_rr else None,
            3,
        ),
        "globalMinimumPeakDistanceSamples": global_refractory,
        "globalMinimumPeakDistanceSeconds": _round(global_refractory / sampling_rate_hz, 4),
        "suppressedDuplicatePeakCount": len(suppressed),
        "suppressedCandidates": suppressed,
        "rPeakSamples": r_peak_samples,
        "rPeakTimesSeconds": r_peak_times,
        "detectedBeatCount": len(r_peak_samples),
        "datasetAnnotationSample": int(dataset_annotation_sample)
        if dataset_annotation_sample is not None
        else None,
        "nearestDetectedRPeakSample": nearest_peak,
        "triggerAlignmentErrorSamples": trigger_alignment_error_samples,
        "triggerAlignmentErrorMilliseconds": _round(
            trigger_alignment_error_samples / sampling_rate_hz * 1000.0
            if trigger_alignment_error_samples is not None
            else None,
            3,
        ),
        "triggerBeatIndex": trigger_beat_index,
        "rPeakAgreement": _round(median_support_fraction, 4),
        "validation": {
            "status": validation_status,
            "minimumRrMilliseconds": _round(minimum_rr_ms, 3),
            "maximumInstantaneousHeartRateBpm": _round(maximum_hr, 3),
            "medianCalculatedHeartRateBpm": _round(median_hr, 3),
            "metadataHeartRateBpm": _round(metadata_heart_rate_bpm, 3),
            "metadataHeartRateDifferenceFraction": _round(metadata_difference_fraction, 4),
            "metadataUsedForDetection": False,
            "reasons": validation_reasons,
        },
        "confidence": _round(confidence, 2),
    }