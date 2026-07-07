from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
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

@dataclass
class CyclicWaveformBuffer:
    sample_rate: int
    source_sample_rate: float
    record_name: str
    pn_dir: str
    normalized_signals: np.ndarray
    physical_signals_mv: np.ndarray
    lead_ids: list[str]
    lead_names: list[str]
    estimated_hr: int
    buffer_seconds: int

    @property
    def total_samples(self) -> int:
        return int(self.physical_signals_mv.shape[0])

    def read(self, cursor: int, batch_size: int) -> tuple[int, np.ndarray, np.ndarray]:
        total = self.total_samples

        if total <= 0:
            raise RuntimeError("Cyclic waveform buffer is empty.")

        start = cursor % total
        end = start + batch_size

        if end <= total:
            normalized_batch = self.normalized_signals[start:end]
            physical_batch = self.physical_signals_mv[start:end]
        else:
            first_normalized = self.normalized_signals[start:total]
            second_normalized = self.normalized_signals[0 : end - total]

            first_physical = self.physical_signals_mv[start:total]
            second_physical = self.physical_signals_mv[0 : end - total]

            normalized_batch = np.concatenate(
                [first_normalized, second_normalized],
                axis=0,
            )

            physical_batch = np.concatenate(
                [first_physical, second_physical],
                axis=0,
            )

        next_cursor = (cursor + batch_size) % total

        return next_cursor, normalized_batch, physical_batch

_CACHE_LOCK = RLock()
_CACHED_DATA: PhysioNetWaveformData | None = None
_CACHED_ERROR: str | None = None
_CACHED_CYCLIC_BUFFER: CyclicWaveformBuffer | None = None


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

_CACHED_CYCLIC_BUFFER: CyclicWaveformBuffer | None = None


def apply_auto_gain_demo_profile(
    signals_mv: np.ndarray,
    sample_rate: int,
    lead_ids: list[str],
) -> np.ndarray:
    """
    Testing-only amplitude profile.

    This keeps the ECG morphology from PhysioNet but changes amplitude per lead
    at different time segments so the frontend auto-gain can be visibly tested.
    Do not enable this for real clinical/device data.
    """
    output = signals_mv.copy()
    total_samples = output.shape[0]

    # Fractions of the 1-minute cyclic buffer.
    # Each lead changes at different times, so one waveform can switch gain
    # while the others stay unchanged.
    profiles = {
        "lead1": [
            (0.00, 0.25, 1.0),
            (0.25, 0.50, 0.35),
            (0.50, 0.75, 2.4),
            (0.75, 1.00, 1.0),
        ],
        "lead2": [
            (0.00, 0.20, 0.45),
            (0.20, 0.55, 1.0),
            (0.55, 0.80, 2.2),
            (0.80, 1.00, 0.65),
        ],
        "lead3": [
            (0.00, 0.40, 0.30),
            (0.40, 0.70, 1.0),
            (0.70, 1.00, 1.8),
        ],
        "avr": [
            (0.00, 0.30, 2.0),
            (0.30, 0.65, 0.55),
            (0.65, 1.00, 1.2),
        ],
        "avl": [
            (0.00, 0.35, 0.35),
            (0.35, 0.65, 1.0),
            (0.65, 1.00, 2.3),
        ],
        "avf": [
            (0.00, 0.25, 1.0),
            (0.25, 0.60, 0.30),
            (0.60, 0.85, 2.5),
            (0.85, 1.00, 0.75),
        ],
        "v1": [
            (0.00, 0.30, 2.2),
            (0.30, 0.70, 1.0),
            (0.70, 1.00, 0.35),
        ],
    }

    for lead_id, segments in profiles.items():
        if lead_id not in lead_ids:
            continue

        column_index = lead_ids.index(lead_id)

        for start_fraction, end_fraction, multiplier in segments:
            start = int(total_samples * start_fraction)
            end = int(total_samples * end_fraction)

            output[start:end, column_index] *= multiplier

    print(
        "[KGEN AUTO GAIN DEMO PROFILE]",
        f"sample_rate={sample_rate}",
        f"samples={total_samples}",
        f"leads={lead_ids}",
    )

    return output.astype(np.float32)


def build_cyclic_buffer_from_physionet(
    data: PhysioNetWaveformData,
    buffer_seconds: int,
) -> CyclicWaveformBuffer:
    target_samples = int(data.sample_rate * buffer_seconds)

    if target_samples <= 0:
        raise RuntimeError("WAVEFORM_TEST_BUFFER_SECONDS must be greater than 0.")

    source_samples = data.physical_signals_mv.shape[0]

    if source_samples <= 0:
        raise RuntimeError("PhysioNet source signal is empty.")

    repeat_count = int(np.ceil(target_samples / source_samples))

    physical_1min = np.tile(data.physical_signals_mv, (repeat_count, 1))[
    :target_samples
]

    if settings.WAVEFORM_TEST_AUTO_GAIN_DEMO:
        physical_1min = apply_auto_gain_demo_profile(
        signals_mv=physical_1min,
        sample_rate=data.sample_rate,
        lead_ids=data.lead_ids,
    )

        normalized_1min = normalize_for_webgl(physical_1min)
    else:
     normalized_1min = np.tile(data.normalized_signals, (repeat_count, 1))[
        :target_samples
    ]

    print(
        "[KGEN CYCLIC BUFFER READY]",
        f"record={data.record_name}",
        f"sample_rate={data.sample_rate}",
        f"buffer_seconds={buffer_seconds}",
        f"samples={target_samples}",
        f"repeated_source={repeat_count}x",
    )

    return CyclicWaveformBuffer(
        sample_rate=data.sample_rate,
        source_sample_rate=data.source_sample_rate,
        record_name=data.record_name,
        pn_dir=data.pn_dir,
        normalized_signals=normalized_1min.astype(np.float32),
        physical_signals_mv=physical_1min.astype(np.float32),
        lead_ids=data.lead_ids,
        lead_names=data.lead_names,
        estimated_hr=data.estimated_hr,
        buffer_seconds=buffer_seconds,
    )


def get_cyclic_waveform_buffer() -> CyclicWaveformBuffer:
    global _CACHED_CYCLIC_BUFFER

    with _CACHE_LOCK:
        if _CACHED_CYCLIC_BUFFER is not None:
            return _CACHED_CYCLIC_BUFFER

        data = get_physionet_waveform_data()

        _CACHED_CYCLIC_BUFFER = build_cyclic_buffer_from_physionet(
            data=data,
            buffer_seconds=settings.WAVEFORM_TEST_BUFFER_SECONDS,
        )

        return _CACHED_CYCLIC_BUFFER


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
    buffer = get_cyclic_waveform_buffer()

    next_cursor, normalized_batch, physical_batch = buffer.read(
        cursor=cursor,
        batch_size=batch_size,
    )

    leads: dict[str, list[float]] = {}
    leads_mv: dict[str, list[float]] = {}
    latest_mv: dict[str, float] = {}

    for column_index, lead_id in enumerate(buffer.lead_ids):
        leads[lead_id] = [
            round(float(value), 5)
            for value in normalized_batch[:, column_index]
        ]

        leads_mv[lead_id] = [
            round(float(value), 5)
            for value in physical_batch[:, column_index]
        ]

        latest_mv[lead_id] = round(float(physical_batch[-1, column_index]), 3)

    heart_rate = buffer.estimated_hr
    respiratory_rate = max(10, min(32, round(heart_rate / 4.2)))
    phase_seconds = cursor / max(buffer.sample_rate, 1)

    spo2_base = 97 if heart_rate < 110 else 94
    spo2 = round(
        max(
            92,
            min(
                100,
                spo2_base
                + 0.8 * np.sin(phase_seconds / 2.8)
                + 0.3 * np.sin(phase_seconds / 0.9),
            ),
        )
    )

    respiratory_rate = max(
        10,
        min(
            32,
            round((heart_rate / 4.2) + 1.5 * np.sin(phase_seconds / 4.5)),
        ),
    )

    systolic = round(118 + 5 * np.sin(phase_seconds / 5.5))
    diastolic = round(78 + 3 * np.sin(phase_seconds / 6.0))
    temperature = round(37.0 + 0.15 * np.sin(phase_seconds / 12.0), 1)
   
    
    elapsed_seconds = cursor / max(buffer.sample_rate, 1)
    loop_progress = cursor / max(buffer.total_samples, 1)

    if loop_progress < 0.25:
        segment_name = "baseline rhythm"
        segment_message = "Clean ECG segment from PTB-XL record."
    elif loop_progress < 0.50:
        segment_name = "amplitude variation"
        segment_message = "Lead amplitude changes demonstrate per-lead gain handling."
    elif loop_progress < 0.75:
        segment_name = "oxygenation watch"
        segment_message = "SpO2 widget shows live current-point movement."
    else:
        segment_name = "loop reset preview"
        segment_message = "Cyclic buffer is about to wrap without breaking the stream."

    return {
        "source": "physionet-ptb-xl",
        "record": buffer.record_name,
        "status": "connected",
        "receivedAt": now_iso(),
        "sampleRate": buffer.sample_rate,
        "sourceSampleRate": buffer.source_sample_rate,
        "batchSize": batch_size,
        "cursor": cursor,
        "nextCursor": next_cursor,
        "bufferSeconds": buffer.buffer_seconds,
        "bufferSamples": buffer.total_samples,
        "xAxis": {
            "type": "time",
            "unit": "seconds",
            "sampleRate": buffer.sample_rate,
            "secondsVisible": float(settings.WAVEFORM_VISIBLE_SECONDS),
            "samplePeriodMs": round(1000 / buffer.sample_rate, 3),
            "paperSpeedMmPerSec": 25,
            "minorBoxSeconds": 0.04,
            "majorBoxSeconds": 0.20,
        },
        "yAxis": {
            "type": "voltage",
            "unit": "mV",
            "defaultGainMmPerMv": 10,
            "allowedGainMmPerMv": [5, 10, 20],
            "minorBoxMv": 0.1,
            "majorBoxMv": 0.5,
            "autoScale": False,
        },
        "leads": leads,
        "leadsMv": leads_mv,
        "leadNames": {
            lead_id: lead_name
            for lead_id, lead_name in zip(buffer.lead_ids, buffer.lead_names)
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
        "demoPhase": {
    "mode": "cyclic-physionet-demo",
    "segment": segment_name,
    "elapsedSeconds": round(elapsed_seconds, 1),
    "loopProgressPercent": round(loop_progress * 100),
    "message": segment_message,
},
    }