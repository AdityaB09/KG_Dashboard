from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any

import numpy as np
import wfdb

from app.config import settings


LEAD_CONFIG = [
    ("lead1", "I"),
    ("lead2", "II"),
    ("lead3", "III"),
    ("avr", "AVR"),
    ("avl", "AVL"),
    ("avf", "AVF"),
    ("v1", "V1"),
]


@dataclass
class PhysioNetWaveformData:
    sample_rate: int
    source_sample_rate: float
    record_name: str
    pn_dir: str
    normalized_signals: np.ndarray
    physical_signals_mv: np.ndarray
    lead_ids: list[str]
    lead_names: list[str]
    estimated_hr: int


_CACHE_LOCK = Lock()
_CACHED_DATA: PhysioNetWaveformData | None = None
_CACHED_ERROR: str | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_lead_name(name: str) -> str:
    return "".join(ch for ch in str(name).upper() if ch.isalnum())


def linear_resample(signals: np.ndarray, source_fs: float, target_fs: int) -> np.ndarray:
    if int(round(source_fs)) == int(target_fs):
        return signals.astype(np.float32)

    duration_seconds = signals.shape[0] / float(source_fs)

    old_t = np.arange(signals.shape[0], dtype=np.float64) / float(source_fs)
    new_t = np.arange(0, duration_seconds, 1.0 / float(target_fs), dtype=np.float64)

    resampled = []

    for channel_index in range(signals.shape[1]):
        resampled.append(np.interp(new_t, old_t, signals[:, channel_index]))

    return np.stack(resampled, axis=1).astype(np.float32)


def normalize_for_webgl(signals_mv: np.ndarray) -> np.ndarray:
    centered = signals_mv - np.median(signals_mv, axis=0, keepdims=True)

    scale = np.percentile(np.abs(centered), 98, axis=0)
    scale = np.maximum(scale, 0.25)

    normalized = centered / (scale * 1.25)

    return np.clip(normalized, -1.0, 1.0).astype(np.float32)


def estimate_hr_from_lead_ii(lead_ii_mv: np.ndarray, fs: int) -> int:
    centered = lead_ii_mv - np.median(lead_ii_mv)

    if len(centered) < fs:
        return 72

    threshold = np.percentile(centered, 94)
    min_distance = int(0.30 * fs)

    peaks: list[int] = []
    last_peak = -min_distance

    for index in range(1, len(centered) - 1):
        if index - last_peak < min_distance:
            continue

        if (
            centered[index] > threshold
            and centered[index] >= centered[index - 1]
            and centered[index] >= centered[index + 1]
        ):
            peaks.append(index)
            last_peak = index

    if len(peaks) < 2:
        return 72

    rr_intervals = np.diff(np.array(peaks)) / float(fs)
    rr_intervals = rr_intervals[(rr_intervals >= 0.35) & (rr_intervals <= 1.8)]

    if len(rr_intervals) == 0:
        return 72

    bpm = 60.0 / float(np.median(rr_intervals))

    return int(max(35, min(180, round(bpm))))


def load_record_from_physionet(
    *,
    record_name: str,
    pn_dir: str,
    target_sample_rate: int,
) -> PhysioNetWaveformData:
    print(
        "[KGEN PHYSIONET LOAD START]",
        f"record={record_name}",
        f"pn_dir={pn_dir}",
        f"target_sample_rate={target_sample_rate}",
    )

    record = wfdb.rdrecord(
        record_name,
        pn_dir=pn_dir,
        physical=True,
    )

    if record.p_signal is None:
        raise RuntimeError("PhysioNet returned no physical ECG signal.")

    raw_signals = np.asarray(record.p_signal, dtype=np.float32)
    source_fs = float(record.fs)

    available_leads = {
        normalize_lead_name(name): index
        for index, name in enumerate(record.sig_name)
    }

    selected_indexes = []
    selected_ids = []
    selected_names = []

    for lead_id, expected_name in LEAD_CONFIG:
        normalized_expected = normalize_lead_name(expected_name)

        if normalized_expected not in available_leads:
            raise RuntimeError(
                f"Lead {expected_name} was not found. "
                f"Available leads: {record.sig_name}"
            )

        selected_indexes.append(available_leads[normalized_expected])
        selected_ids.append(lead_id)
        selected_names.append(expected_name)

    physical_mv = raw_signals[:, selected_indexes]

    physical_resampled_mv = linear_resample(
        physical_mv,
        source_fs=source_fs,
        target_fs=target_sample_rate,
    )

    normalized = normalize_for_webgl(physical_resampled_mv)

    lead_ii_index = selected_ids.index("lead2")
    estimated_hr = estimate_hr_from_lead_ii(
        physical_resampled_mv[:, lead_ii_index],
        target_sample_rate,
    )

    print(
        "[KGEN PHYSIONET LOAD OK]",
        f"record={record_name}",
        f"source_fs={source_fs}",
        f"target_fs={target_sample_rate}",
        f"shape={physical_resampled_mv.shape}",
        f"leads={selected_names}",
        f"estimated_hr={estimated_hr}",
    )

    return PhysioNetWaveformData(
        sample_rate=target_sample_rate,
        source_sample_rate=source_fs,
        record_name=record_name,
        pn_dir=pn_dir,
        normalized_signals=normalized,
        physical_signals_mv=physical_resampled_mv,
        lead_ids=selected_ids,
        lead_names=selected_names,
        estimated_hr=estimated_hr,
    )


def get_physionet_waveform_data() -> PhysioNetWaveformData:
    global _CACHED_DATA
    global _CACHED_ERROR

    with _CACHE_LOCK:
        if _CACHED_DATA is not None:
            return _CACHED_DATA

        target_sample_rate = int(settings.WAVEFORM_SAMPLE_RATE)

        attempts = [
            {
                "record_name": settings.PHYSIONET_RECORD,
                "pn_dir": settings.PHYSIONET_DB,
            },
            {
                "record_name": settings.PHYSIONET_FALLBACK_RECORD,
                "pn_dir": settings.PHYSIONET_FALLBACK_DB,
            },
        ]

        errors = []

        for attempt in attempts:
            try:
                _CACHED_DATA = load_record_from_physionet(
                    record_name=attempt["record_name"],
                    pn_dir=attempt["pn_dir"],
                    target_sample_rate=target_sample_rate,
                )

                _CACHED_ERROR = None
                return _CACHED_DATA

            except Exception as error:
                message = (
                    f"record={attempt['record_name']}, "
                    f"pn_dir={attempt['pn_dir']}, "
                    f"error={type(error).__name__}: {error}"
                )

                print("[KGEN PHYSIONET LOAD FAILED]", message)
                errors.append(message)

        _CACHED_ERROR = " | ".join(errors)

        raise RuntimeError(
            "Could not load PhysioNet waveform record. "
            f"Details: {_CACHED_ERROR}"
        )




def slice_circular(signal: np.ndarray, start: int, length: int) -> np.ndarray:
    total = signal.shape[0]
    start = start % total
    end = start + length

    if end <= total:
        return signal[start:end]

    first = signal[start:total]
    second = signal[0 : end - total]

    return np.concatenate([first, second], axis=0)


def build_physionet_frame(
    *,
    cursor: int,
    batch_size: int,
) -> dict[str, Any]:
    data = get_physionet_waveform_data()

    normalized_batch = slice_circular(data.normalized_signals, cursor, batch_size)
    physical_batch = slice_circular(data.physical_signals_mv, cursor, batch_size)

    leads: dict[str, list[float]] = {}
    latest_mv: dict[str, float] = {}

    for column_index, lead_id in enumerate(data.lead_ids):
        leads[lead_id] = [
            round(float(value), 5)
            for value in normalized_batch[:, column_index]
        ]

        latest_mv[lead_id] = round(float(physical_batch[-1, column_index]), 3)

    heart_rate = data.estimated_hr

    respiratory_rate = max(10, min(32, round(heart_rate / 4.2)))
    spo2 = 97 if heart_rate < 110 else 94
    temperature = 37.0 if heart_rate < 110 else 37.4
    systolic = 118 if heart_rate < 110 else 128
    diastolic = 78 if heart_rate < 110 else 86

    return {
        "source": "physionet-ptb-xl",
        "record": data.record_name,
        "status": "connected",
        "receivedAt": now_iso(),
        "sampleRate": data.sample_rate,
        "sourceSampleRate": data.source_sample_rate,
        "batchSize": batch_size,
        "cursor": cursor,
        "xAxis": {
            "type": "time",
            "unit": "seconds",
            "sampleRate": data.sample_rate,
            "secondsVisible": float(settings.WAVEFORM_VISIBLE_SECONDS),
            "samplePeriodMs": round(1000 / data.sample_rate, 3),
            "paperSpeedMmPerSec": 25,
        },
        "yAxis": {
            "type": "voltage",
            "unit": "mV",
            "displayVoltageScaleMmPerMv": 10,
            "webglRange": [-1, 1],
        },
        "leads": leads,
        "leadNames": {
            lead_id: lead_name
            for lead_id, lead_name in zip(data.lead_ids, data.lead_names)
        },
        "latestMv": latest_mv,
        "vitals": {
            "heartRate": heart_rate,
            "spo2": spo2,
            "systolic": systolic,
            "diastolic": diastolic,
            "respiratoryRate": respiratory_rate,
            "temperature": temperature,
        },
    }