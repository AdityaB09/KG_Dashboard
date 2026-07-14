from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
import numpy as np

from app.config import settings
from app.physionet_waveforms import (
    estimate_hr_from_lead_ii,
    normalize_for_webgl,
)


API_LEADS = {
    "leadI": "lead1",
    "leadII": "lead2",
    "leadIII": "lead3",
    "avr": "avr",
    "avl": "avl",
    "avf": "avf",
}

LEAD_NAMES = {
    "lead1": "Lead I",
    "lead2": "Lead II",
    "lead3": "Lead III",
    "avr": "aVR",
    "avl": "aVL",
    "avf": "aVF",
}


@dataclass
class ApiRangeBuffer:
    sample_rate: int
    source_sample_rate: float
    signals_mv: np.ndarray
    normalized_signals: np.ndarray
    ppg_signal: np.ndarray
    spo_values: np.ndarray
    temperature_values: np.ndarray
    estimated_hr: int

    @property
    def total_samples(self) -> int:
        return int(self.signals_mv.shape[0])

    @property
    def buffer_seconds(self) -> float:
        return self.total_samples / max(self.sample_rate, 1)

    def read(
        self,
        cursor: int,
        batch_size: int,
    ) -> tuple[int, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        indexes = (
            np.arange(batch_size, dtype=np.int64) + cursor
        ) % self.total_samples

        next_cursor = (cursor + batch_size) % self.total_samples

        return (
            next_cursor,
            self.normalized_signals[indexes],
            self.signals_mv[indexes],
            self.ppg_signal[indexes],
            self.spo_values[indexes],
            self.temperature_values[indexes],
        )


_CACHE_LOCK = asyncio.Lock()
_CACHED_BUFFER: ApiRangeBuffer | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_timestamp(value: Any) -> float:
    text = str(value).strip().replace("Z", "+00:00")

    if len(text) >= 5 and text[-5] in "+-" and text[-3] != ":":
        text = f"{text[:-2]}:{text[-2:]}"

    parsed = datetime.fromisoformat(text)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.timestamp()


def parse_series(
    payload: dict[str, Any],
    key: str,
    required: bool = True,
) -> tuple[np.ndarray, np.ndarray] | None:
    items = payload.get(key)

    if not isinstance(items, list) or not items:
        if required:
            raise RuntimeError(f"API response is missing {key}.")
        return None

    points: dict[float, float] = {}

    for item in items:
        if not isinstance(item, dict):
            continue

        try:
            timestamp = parse_timestamp(item.get("x"))
            value = float(item.get("y"))
        except (TypeError, ValueError):
            continue

        if np.isfinite(timestamp) and np.isfinite(value):
            points[timestamp] = value

    if len(points) < 2:
        if required:
            raise RuntimeError(f"API response has insufficient {key} data.")
        return None

    times = np.asarray(sorted(points), dtype=np.float64)
    values = np.asarray([points[item] for item in times], dtype=np.float32)

    return times, values


def resample_series(
    series: tuple[np.ndarray, np.ndarray] | None,
    target_times: np.ndarray,
    default_value: float = np.nan,
) -> np.ndarray:
    if series is None:
        return np.full(target_times.shape, default_value, dtype=np.float32)

    times, values = series

    return np.interp(
        target_times,
        times,
        values,
    ).astype(np.float32)


def normalize_ppg(values: np.ndarray) -> np.ndarray:
    finite_values = values[np.isfinite(values)]

    if not finite_values.size:
        return np.zeros(values.shape, dtype=np.float32)

    centered = values - np.nanmedian(values)
    scale = np.nanpercentile(np.abs(centered), 98)

    if not np.isfinite(scale) or scale <= 0:
        scale = 1.0

    return np.clip(centered / scale, -1, 1).astype(np.float32)


def circular_slice(
    values: np.ndarray,
    start: int,
    length: int,
) -> np.ndarray:
    indexes = (
        np.arange(length, dtype=np.int64) + start
    ) % len(values)

    return values[indexes]


def last_valid_value(
    values: np.ndarray,
    low: float,
    high: float,
    decimals: int = 0,
) -> float | int | None:
    valid = values[
        np.isfinite(values)
        & (values >= low)
        & (values <= high)
    ]

    if not valid.size:
        return None

    value = float(valid[-1])

    if decimals == 0:
        return int(round(value))

    return round(value, decimals)


async def fetch_api_payload() -> dict[str, Any]:
    required_settings = {
        "API_RANGE_URL": settings.API_RANGE_URL,
        "API_RANGE_USER_ID": settings.API_RANGE_USER_ID,
        "API_RANGE_DEVICE_ID": settings.API_RANGE_DEVICE_ID,
        "API_RANGE_FROM_TIMESTAMP": settings.API_RANGE_FROM_TIMESTAMP,
        "API_RANGE_TO_TIMESTAMP": settings.API_RANGE_TO_TIMESTAMP,
    }

    missing = [
        name
        for name, value in required_settings.items()
        if not value
    ]

    if missing:
        raise RuntimeError(
            f"Missing API Range settings: {', '.join(missing)}"
        )

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    if settings.API_RANGE_API_KEY:
        headers[settings.API_RANGE_API_KEY_HEADER] = (
            settings.API_RANGE_API_KEY
        )

    body = {
        "user_id": settings.API_RANGE_USER_ID,
        "device_id": settings.API_RANGE_DEVICE_ID,
        "from_timestamp": settings.API_RANGE_FROM_TIMESTAMP,
        "to_timestamp": settings.API_RANGE_TO_TIMESTAMP,
    }

    async with httpx.AsyncClient(
        timeout=settings.API_RANGE_TIMEOUT_SECONDS
    ) as client:
        response = await client.post(
            settings.API_RANGE_URL,
            json=body,
            headers=headers,
        )

    response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, dict):
        raise RuntimeError("API Range response must be a JSON object.")

    return payload


async def load_api_range_buffer() -> ApiRangeBuffer:
    payload = await fetch_api_payload()

    lead_series = {
        api_key: parse_series(payload, api_key)
        for api_key in API_LEADS
    }

    common_start = max(
        series[0][0]
        for series in lead_series.values()
        if series is not None
    )

    common_end = min(
        series[0][-1]
        for series in lead_series.values()
        if series is not None
    )

    if common_end <= common_start:
        raise RuntimeError("API lead timestamps do not overlap.")

    sample_rate = int(settings.WAVEFORM_SAMPLE_RATE)
    sample_count = max(
        2,
        int(np.floor((common_end - common_start) * sample_rate)) + 1,
    )

    target_times = (
        common_start
        + np.arange(sample_count, dtype=np.float64) / sample_rate
    )

    signal_columns = []

    for api_key in API_LEADS:
        values = resample_series(
            lead_series[api_key],
            target_times,
        )
        signal_columns.append(
            values * settings.API_RANGE_ECG_VALUE_TO_MV
        )

    signals_mv = np.stack(signal_columns, axis=1).astype(np.float32)
    normalized_signals = normalize_for_webgl(signals_mv)

    ppg_series = parse_series(
        payload,
        "ppgIR",
        required=False,
    )

    if ppg_series is None:
        ppg_series = parse_series(
            payload,
            "ppgred",
            required=False,
        )

    ppg_signal = normalize_ppg(
        resample_series(ppg_series, target_times, 0.0)
    )

    spo_values = resample_series(
        parse_series(
            payload,
            "oxygenSaturation",
            required=False,
        ),
        target_times,
    )

    temperature_values = resample_series(
        parse_series(
            payload,
            "temperature",
            required=False,
        ),
        target_times,
    )

    source_times = lead_series["leadI"][0]
    differences = np.diff(source_times)
    valid_differences = differences[differences > 0]

    source_sample_rate = (
        float(1 / np.median(valid_differences))
        if valid_differences.size
        else float(sample_rate)
    )

    estimated_hr = estimate_hr_from_lead_ii(
        signals_mv[:, 1],
        sample_rate,
    )

    return ApiRangeBuffer(
        sample_rate=sample_rate,
        source_sample_rate=source_sample_rate,
        signals_mv=signals_mv,
        normalized_signals=normalized_signals,
        ppg_signal=ppg_signal,
        spo_values=spo_values,
        temperature_values=temperature_values,
        estimated_hr=estimated_hr,
    )


async def get_api_range_buffer() -> ApiRangeBuffer:
    global _CACHED_BUFFER

    if _CACHED_BUFFER is not None:
        return _CACHED_BUFFER

    async with _CACHE_LOCK:
        if _CACHED_BUFFER is None:
            _CACHED_BUFFER = await load_api_range_buffer()

    return _CACHED_BUFFER


async def build_api_range_frame(
    cursor: int,
    batch_size: int,
) -> dict[str, Any]:
    buffer = await get_api_range_buffer()

    (
        next_cursor,
        normalized_batch,
        physical_batch,
        _,
        spo_batch,
        temperature_batch,
    ) = buffer.read(cursor, batch_size)

    lead_ids = list(API_LEADS.values())

    leads = {
        lead_id: [
            round(float(value), 5)
            for value in normalized_batch[:, index]
        ]
        for index, lead_id in enumerate(lead_ids)
    }

    leads_mv = {
        lead_id: [
            round(float(value), 5)
            for value in physical_batch[:, index]
        ]
        for index, lead_id in enumerate(lead_ids)
    }

    latest_mv = {
        lead_id: round(
            float(physical_batch[-1, index]),
            3,
        )
        for index, lead_id in enumerate(lead_ids)
    }

    ppg_points = min(
        buffer.total_samples,
        buffer.sample_rate * 4,
    )

    ppg_trace = circular_slice(
        buffer.ppg_signal,
        next_cursor - ppg_points,
        ppg_points,
    )

    return {
        "source": "api-range",
        "record": (
            f"{settings.API_RANGE_FROM_TIMESTAMP}"
            f"__{settings.API_RANGE_TO_TIMESTAMP}"
        ),
        "status": "connected",
        "receivedAt": now_iso(),
        "sampleRate": buffer.sample_rate,
        "sourceSampleRate": round(buffer.source_sample_rate, 3),
        "batchSize": batch_size,
        "cursor": cursor,
        "nextCursor": next_cursor,
        "bufferSeconds": round(buffer.buffer_seconds, 3),
        "bufferSamples": buffer.total_samples,
        "xAxis": {
            "type": "time",
            "unit": "seconds",
            "sampleRate": buffer.sample_rate,
            "secondsVisible": float(
                settings.WAVEFORM_VISIBLE_SECONDS
            ),
            "samplePeriodMs": round(
                1000 / buffer.sample_rate,
                3,
            ),
            "paperSpeedMmPerSec": 25,
            "minorBoxSeconds": 0.04,
            "majorBoxSeconds": 0.20,
        },
        "yAxis": {
            "type": "voltage",
            "unit": "mV",
            "defaultGainMmPerMv": 10,
            "allowedGainMmPerMv": [
                0.25,
                0.5,
                1,
                2.5,
                5,
                10,
                20,
                40,
            ],
            "minorBoxMv": 0.1,
            "majorBoxMv": 0.5,
            "autoScale": True,
            "zeroMvOnSolidGrid": True,
        },
        "leads": leads,
        "leadsMv": leads_mv,
        "leadNames": LEAD_NAMES,
        "latestMv": latest_mv,
        "vitals": {
            "heartRate": buffer.estimated_hr,
            "spo2": last_valid_value(
                spo_batch,
                50,
                100,
            ),
            "ppgTrace": [
                round(float(value), 4)
                for value in ppg_trace
            ],
            "systolic": None,
            "diastolic": None,
            "respiratoryRate": None,
            "temperature": last_valid_value(
                temperature_batch,
                25,
                45,
                1,
            ),
        },
    }