from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

import numpy as np

from app.config import settings


CSV_LEAD_IDS = ["lead1", "lead2", "lead3", "avr", "avl", "avf"]

CSV_LEAD_NAMES = {
    "lead1": "Lead I",
    "lead2": "Lead II",
    "lead3": "Lead III",
    "avr": "aVR",
    "avl": "aVL",
    "avf": "aVF",
}


@dataclass
class CsvWaveformData:
    sample_rate: int
    source_sample_rate: float
    record_name: str
    lead_signals_mv: np.ndarray
    ppg_signal: np.ndarray
    spo_values: np.ndarray
    lead_ids: list[str]
    lead_names: list[str]
    estimated_hr: int


@dataclass
class CsvCyclicWaveformBuffer:
    sample_rate: int
    source_sample_rate: float
    record_name: str
    lead_signals_mv: np.ndarray
    normalized_signals: np.ndarray
    ppg_signal: np.ndarray
    spo_values: np.ndarray
    lead_ids: list[str]
    lead_names: list[str]
    estimated_hr: int
    buffer_seconds: int

    @property
    def total_samples(self) -> int:
        return int(self.lead_signals_mv.shape[0])

    def read(
        self,
        cursor: int,
        batch_size: int,
    ) -> tuple[int, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        total = self.total_samples

        if total <= 0:
            raise RuntimeError("CSV cyclic waveform buffer is empty.")

        start = cursor % total
        end = start + batch_size

        if end <= total:
            physical_batch = self.lead_signals_mv[start:end]
            normalized_batch = self.normalized_signals[start:end]
            ppg_batch = self.ppg_signal[start:end]
            spo_batch = self.spo_values[start:end]
        else:
            first_physical = self.lead_signals_mv[start:total]
            second_physical = self.lead_signals_mv[0 : end - total]

            first_normalized = self.normalized_signals[start:total]
            second_normalized = self.normalized_signals[0 : end - total]

            first_ppg = self.ppg_signal[start:total]
            second_ppg = self.ppg_signal[0 : end - total]

            first_spo = self.spo_values[start:total]
            second_spo = self.spo_values[0 : end - total]

            physical_batch = np.concatenate([first_physical, second_physical], axis=0)
            normalized_batch = np.concatenate([first_normalized, second_normalized], axis=0)
            ppg_batch = np.concatenate([first_ppg, second_ppg], axis=0)
            spo_batch = np.concatenate([first_spo, second_spo], axis=0)

        next_cursor = (cursor + batch_size) % total

        return next_cursor, normalized_batch, physical_batch, ppg_batch, spo_batch


_CACHE_LOCK = RLock()
_CACHED_CSV_DATA: CsvWaveformData | None = None
_CACHED_CSV_BUFFER: CsvCyclicWaveformBuffer | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None

        text = str(value).strip()

        if not text:
            return None

        return float(text)

    except (TypeError, ValueError):
        return None


def linear_resample_by_time(
    time_seconds: np.ndarray,
    signals: np.ndarray,
    target_fs: int,
) -> tuple[np.ndarray, float]:
    if len(time_seconds) < 2:
        raise RuntimeError("CSV needs at least two time samples.")

    time_seconds = time_seconds.astype(np.float64)
    time_seconds = time_seconds - time_seconds[0]

    duration_seconds = float(time_seconds[-1])

    if duration_seconds <= 0:
        raise RuntimeError("CSV time column has invalid duration.")

    dt = np.diff(time_seconds)
    source_fs = 1.0 / float(np.median(dt))

    new_t = np.arange(0, duration_seconds, 1.0 / float(target_fs), dtype=np.float64)

    resampled = []

    for column_index in range(signals.shape[1]):
        resampled.append(np.interp(new_t, time_seconds, signals[:, column_index]))

    return np.stack(resampled, axis=1).astype(np.float32), source_fs


def normalize_for_webgl(signals_mv: np.ndarray) -> np.ndarray:
    centered = signals_mv - np.median(signals_mv, axis=0, keepdims=True)

    scale = np.percentile(np.abs(centered), 98, axis=0)
    scale = np.maximum(scale, 0.25)

    normalized = centered / (scale * 1.25)

    return np.clip(normalized, -1.0, 1.0).astype(np.float32)


def convert_ecg_counts_to_mv(raw_ecg_counts: np.ndarray) -> np.ndarray:
    centered = raw_ecg_counts - np.median(raw_ecg_counts, axis=0, keepdims=True)

    counts_per_mv = float(settings.WAVEFORM_CSV_ECG_COUNTS_PER_MV)

    if counts_per_mv > 0:
        return (centered / counts_per_mv).astype(np.float32)

    # Demo calibration:
    # The CSV ECG values appear to be ADC counts, not direct mV.
    # Without hardware gain/counts-per-mV, we convert counts into a stable
    # mV-like display scale while preserving morphology and relative amplitude.
    robust_counts = np.percentile(np.abs(centered), 99)

    if robust_counts <= 0:
        robust_counts = 1.0

    target_p99_mv = 0.95

    return (centered * (target_p99_mv / robust_counts)).astype(np.float32)


def normalize_ppg(raw_ppg: np.ndarray) -> np.ndarray:
    values = raw_ppg.astype(np.float32)

    finite = values[np.isfinite(values)]

    if finite.size == 0:
        return np.zeros_like(values, dtype=np.float32)

    # Remove obvious dropouts/extreme device spikes for display.
    low = np.percentile(finite, 5)
    high = np.percentile(finite, 95)

    if high <= low:
        return np.zeros_like(values, dtype=np.float32)

    clipped = np.clip(values, low, high)
    normalized = (clipped - low) / (high - low)

    return np.clip(normalized, 0.0, 1.0).astype(np.float32)


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


def load_csv_rows(path: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    csv_path = Path(path)

    if not csv_path.exists():
        raise RuntimeError(f"CSV waveform file not found: {path}")

    time_values: list[float] = []
    ecg_rows: list[list[float]] = []
    ppg_values: list[float] = []
    spo_values: list[float] = []

    with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as file:
        reader = csv.DictReader(file)

        for row in reader:
            if not row:
                continue

            time_value = safe_float(row.get("Time_Data"))
            ch1 = safe_float(row.get("ECG_CH1_Data"))
            ch2 = safe_float(row.get("ECG_CH2_Data"))
            ch3 = safe_float(row.get("ECG_CH3_Data"))
            ppg = safe_float(row.get("PPG_Data"))
            spo = safe_float(row.get("SPO_Value"))

            if time_value is None or ch1 is None or ch2 is None or ch3 is None:
                continue

            time_values.append(time_value)
            ecg_rows.append([ch1, ch2, ch3])
            ppg_values.append(ppg if ppg is not None else 0.0)
            spo_values.append(spo if spo is not None else 100.0)

    if len(time_values) < 2:
        raise RuntimeError(f"CSV waveform file has too few valid rows: {path}")

    time_array = np.asarray(time_values, dtype=np.float64)
    ecg_array = np.asarray(ecg_rows, dtype=np.float32)
    ppg_array = np.asarray(ppg_values, dtype=np.float32)
    spo_array = np.asarray(spo_values, dtype=np.float32)

    order = np.argsort(time_array)

    return (
        time_array[order],
        ecg_array[order],
        ppg_array[order],
        spo_array[order],
    )


def load_csv_waveform_data() -> CsvWaveformData:
    paths = settings.WAVEFORM_CSV_PATHS

    if not paths:
        raise RuntimeError(
            "WAVEFORM_CSV_PATHS is empty. Add at least one CSV path in .env."
        )

    active_index = max(0, min(int(settings.WAVEFORM_CSV_ACTIVE_INDEX), len(paths) - 1))
    active_path = paths[active_index]

    target_fs = int(settings.WAVEFORM_SAMPLE_RATE)

    print(
        "[KGEN CSV WAVEFORM LOAD START]",
        f"path={active_path}",
        f"target_fs={target_fs}",
    )

    time_array, raw_ecg_counts, raw_ppg, raw_spo = load_csv_rows(active_path)

    combined = np.column_stack([raw_ecg_counts, raw_ppg, raw_spo])

    resampled, source_fs = linear_resample_by_time(
        time_array,
        combined,
        target_fs=target_fs,
    )

    raw_ecg_resampled = resampled[:, 0:3]
    raw_ppg_resampled = resampled[:, 3]
    raw_spo_resampled = resampled[:, 4]

    limb_mv = convert_ecg_counts_to_mv(raw_ecg_resampled)

    lead_i = limb_mv[:, 0]
    lead_ii = limb_mv[:, 1]
    lead_iii = limb_mv[:, 2]

    # Augmented limb leads from Lead I and Lead II.
    avr = -((lead_i + lead_ii) / 2.0)
    avl = lead_i - (lead_ii / 2.0)
    avf = lead_ii - (lead_i / 2.0)

    lead_signals_mv = np.stack(
        [
            lead_i,
            lead_ii,
            lead_iii,
            avr,
            avl,
            avf,
        ],
        axis=1,
    ).astype(np.float32)

    ppg_signal = normalize_ppg(raw_ppg_resampled)

    spo_values = np.clip(raw_spo_resampled, 0, 100).astype(np.float32)

    estimated_hr = estimate_hr_from_lead_ii(lead_ii, target_fs)

    record_name = Path(active_path).name

    print(
        "[KGEN CSV WAVEFORM LOAD OK]",
        f"record={record_name}",
        f"source_fs={round(source_fs, 3)}",
        f"target_fs={target_fs}",
        f"samples={lead_signals_mv.shape[0]}",
        f"estimated_hr={estimated_hr}",
    )

    return CsvWaveformData(
        sample_rate=target_fs,
        source_sample_rate=source_fs,
        record_name=record_name,
        lead_signals_mv=lead_signals_mv,
        ppg_signal=ppg_signal,
        spo_values=spo_values,
        lead_ids=CSV_LEAD_IDS,
        lead_names=[CSV_LEAD_NAMES[lead_id] for lead_id in CSV_LEAD_IDS],
        estimated_hr=estimated_hr,
    )


def get_csv_waveform_data() -> CsvWaveformData:
    global _CACHED_CSV_DATA

    with _CACHE_LOCK:
        if _CACHED_CSV_DATA is not None:
            return _CACHED_CSV_DATA

        _CACHED_CSV_DATA = load_csv_waveform_data()

        return _CACHED_CSV_DATA


def build_cyclic_buffer_from_csv(
    data: CsvWaveformData,
    buffer_seconds: int,
) -> CsvCyclicWaveformBuffer:
    target_samples = int(data.sample_rate * buffer_seconds)

    if target_samples <= 0:
        raise RuntimeError("WAVEFORM_TEST_BUFFER_SECONDS must be greater than 0.")

    source_samples = data.lead_signals_mv.shape[0]

    if source_samples <= 0:
        raise RuntimeError("CSV source signal is empty.")

    repeat_count = int(np.ceil(target_samples / source_samples))

    physical_1min = np.tile(data.lead_signals_mv, (repeat_count, 1))[:target_samples]
    ppg_1min = np.tile(data.ppg_signal, repeat_count)[:target_samples]
    spo_1min = np.tile(data.spo_values, repeat_count)[:target_samples]

    normalized_1min = normalize_for_webgl(physical_1min)

    print(
        "[KGEN CSV CYCLIC BUFFER READY]",
        f"record={data.record_name}",
        f"sample_rate={data.sample_rate}",
        f"buffer_seconds={buffer_seconds}",
        f"samples={target_samples}",
        f"repeated_source={repeat_count}x",
    )

    return CsvCyclicWaveformBuffer(
        sample_rate=data.sample_rate,
        source_sample_rate=data.source_sample_rate,
        record_name=data.record_name,
        lead_signals_mv=physical_1min.astype(np.float32),
        normalized_signals=normalized_1min.astype(np.float32),
        ppg_signal=ppg_1min.astype(np.float32),
        spo_values=spo_1min.astype(np.float32),
        lead_ids=data.lead_ids,
        lead_names=data.lead_names,
        estimated_hr=data.estimated_hr,
        buffer_seconds=buffer_seconds,
    )


def get_csv_cyclic_waveform_buffer() -> CsvCyclicWaveformBuffer:
    global _CACHED_CSV_BUFFER

    with _CACHE_LOCK:
        if _CACHED_CSV_BUFFER is not None:
            return _CACHED_CSV_BUFFER

        data = get_csv_waveform_data()

        _CACHED_CSV_BUFFER = build_cyclic_buffer_from_csv(
            data=data,
            buffer_seconds=settings.WAVEFORM_TEST_BUFFER_SECONDS,
        )

        return _CACHED_CSV_BUFFER


def slice_circular(signal: np.ndarray, start: int, length: int) -> np.ndarray:
    total = signal.shape[0]

    if total <= 0:
        return np.array([], dtype=np.float32)

    start = start % total
    end = start + length

    if end <= total:
        return signal[start:end]

    first = signal[start:total]
    second = signal[0 : end - total]

    return np.concatenate([first, second], axis=0)


def build_csv_waveform_frame(
    *,
    cursor: int,
    batch_size: int,
) -> dict[str, Any]:
    buffer = get_csv_cyclic_waveform_buffer()

    next_cursor, normalized_batch, physical_batch, ppg_batch, spo_batch = buffer.read(
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

    ppg_window_points = min(buffer.total_samples, int(buffer.sample_rate * 4))
    ppg_trace = slice_circular(
        buffer.ppg_signal,
        next_cursor - ppg_window_points,
        ppg_window_points,
    )

    heart_rate = buffer.estimated_hr
    phase_seconds = cursor / max(buffer.sample_rate, 1)

    spo2 = int(round(float(spo_batch[-1]))) if len(spo_batch) else 100
    spo2 = int(max(0, min(100, spo2)))

    respiratory_rate = max(
        10,
        min(
            32,
            round((heart_rate / 4.2) + 1.2 * np.sin(phase_seconds / 4.8)),
        ),
    )

    systolic = round(118 + 5 * np.sin(phase_seconds / 5.6))
    diastolic = round(78 + 3 * np.sin(phase_seconds / 6.2))
    temperature = round(37.0 + 0.12 * np.sin(phase_seconds / 12.0), 1)

    loop_progress = cursor / max(buffer.total_samples, 1)

    return {
        "source": "csv-device-waveform",
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
            "autoScale": True,
            "zeroMvOnSolidGrid": True,
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
            "ppgTrace": [round(float(value), 4) for value in ppg_trace],
            "systolic": systolic,
            "diastolic": diastolic,
            "respiratoryRate": respiratory_rate,
            "temperature": temperature,
        },
        "demo": {
            "mode": "csv-cyclic-buffer",
            "record": buffer.record_name,
            "loopProgressPercent": round(loop_progress * 100),
            "note": (
                "ECG_CH1/2/3 are streamed from CSV. "
                "aVR/aVL/aVF are calculated from Lead I and Lead II. "
                "PPG mini waveform and SpO2 value are read from CSV."
            ),
        },
    }