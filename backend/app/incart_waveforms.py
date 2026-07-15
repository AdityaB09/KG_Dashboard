from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any

import numpy as np
import wfdb
from scipy.signal import butter, sosfiltfilt
from app.config import settings
from app.physionet_waveforms import (
    estimate_hr_from_lead_ii,
    linear_resample,
    normalize_for_webgl,
)


LEAD_CONFIG = [
    ("lead1", "I"),
    ("lead2", "II"),
    ("lead3", "III"),
    ("avr", "AVR"),
    ("avl", "AVL"),
    ("avf", "AVF"),
    ("v1", "V1"),
    ("v2", "V2"),
    ("v3", "V3"),
    ("v4", "V4"),
    ("v5", "V5"),
    ("v6", "V6"),
]

DISPLAY_LEADS = [
    "lead1",
    "lead2",
    "lead3",
    "avr",
    "avl",
    "avf",
]
BEAT_SYMBOLS = {
    "N",
    "L",
    "R",
    "B",
    "A",
    "a",
    "J",
    "S",
    "V",
    "r",
    "F",
    "e",
    "j",
    "n",
    "E",
    "/",
    "f",
    "Q",
    "?",
}

@dataclass
class IncartBuffer:
    sample_rate: int
    source_sample_rate: float
    record_name: str
    pn_dir: str
    raw_signals_mv: np.ndarray
    physical_signals_mv: np.ndarray
    normalized_signals: np.ndarray
    lead_ids: list[str]
    lead_names: list[str]
    display_indexes: list[int]
    annotation_samples: np.ndarray
    annotation_symbols: np.ndarray
    estimated_hr: int

    @property
    def total_samples(self) -> int:
        return int(self.physical_signals_mv.shape[0])

    @property
    def duration_seconds(self) -> float:
        return self.total_samples / max(self.sample_rate, 1)



    def heart_rate_for_cursor(
    self,
    cursor: int,
    window_seconds: float = 12,
) -> int:
        end_sample = max(0, int(cursor))
        window_samples = int(
            self.sample_rate * window_seconds
        )
        start_sample = max(
            0,
            end_sample - window_samples,
        )

        first_loop = start_sample // self.total_samples
        last_loop = end_sample // self.total_samples

        beat_samples: list[int] = []

        for loop_index in range(
            first_loop,
            last_loop + 1,
        ):
            loop_offset = (
                loop_index * self.total_samples
            )

            absolute_samples = (
                self.annotation_samples
                + loop_offset
            )

            symbol_mask = np.isin(
                self.annotation_symbols,
                list(BEAT_SYMBOLS),
            )

            range_mask = (
                (absolute_samples >= start_sample)
                & (absolute_samples <= end_sample)
            )

            indexes = np.flatnonzero(
                symbol_mask & range_mask
            )

            beat_samples.extend(
                int(absolute_samples[index])
                for index in indexes
            )

        if len(beat_samples) < 2:
            return self.estimated_hr

        rr_intervals = (
            np.diff(
                np.asarray(
                    beat_samples,
                    dtype=np.float64,
                )
            )
            / self.sample_rate
        )

        rr_intervals = rr_intervals[
            (rr_intervals >= 0.3)
            & (rr_intervals <= 2.0)
        ]

        if not rr_intervals.size:
            return self.estimated_hr

        heart_rate = 60 / float(
            np.median(rr_intervals)
        )

        return int(
            np.clip(
                round(heart_rate),
                30,
                220,
            )
        )
    

    def annotations_for_window(
        self,
        cursor: int,
        batch_size: int,
    ) -> list[dict[str, Any]]:
        start = int(cursor)
        end = start + int(batch_size)
        first_loop = start // self.total_samples
        last_loop = (end - 1) // self.total_samples
        output: list[dict[str, Any]] = []

        for loop_index in range(first_loop, last_loop + 1):
            loop_offset = loop_index * self.total_samples
            absolute_samples = self.annotation_samples + loop_offset

            mask = (
                (absolute_samples >= start)
                & (absolute_samples < end)
            )

            indexes = np.flatnonzero(mask)

            for index in indexes:
                record_sample = int(self.annotation_samples[index])
                absolute_sample = int(absolute_samples[index])

                output.append(
                    {
                        "symbol": str(self.annotation_symbols[index]),
                        "sample": record_sample,
                        "absoluteSample": absolute_sample,
                        "frameOffset": absolute_sample - start,
                        "seconds": round(
                            record_sample / self.sample_rate,
                            3,
                        ),
                        "loopNumber": loop_index + 1,
                    }
                )

        return output

    def read(
        self,
        cursor: int,
        batch_size: int,
    ) -> tuple[
        int,
        np.ndarray,
        np.ndarray,
        list[dict[str, Any]],
        int,
    ]:
        absolute_cursor = max(0, int(cursor))

        indexes = (
            np.arange(batch_size, dtype=np.int64)
            + absolute_cursor
        ) % self.total_samples

        normalized_batch = self.normalized_signals[
            indexes
        ][:, self.display_indexes]

        physical_batch = self.physical_signals_mv[
            indexes
        ][:, self.display_indexes]

        annotations = self.annotations_for_window(
            absolute_cursor,
            batch_size,
        )

        loop_number = (
            absolute_cursor // self.total_samples
        ) + 1

        return (
            absolute_cursor + batch_size,
            normalized_batch,
            physical_batch,
            annotations,
            loop_number,
        )


_CACHE_LOCK = RLock()
_CACHED_BUFFER: IncartBuffer | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_lead_name(value: str) -> str:
    return "".join(
        character
        for character in str(value).upper()
        if character.isalnum()
    )


def center_ecg_for_display(
    signals_mv: np.ndarray,
    sample_rate: int,
) -> np.ndarray:
    signals = np.asarray(
        signals_mv,
        dtype=np.float64,
    )

    baseline = np.median(
        signals,
        axis=0,
        keepdims=True,
    )

    centered = signals - baseline

    sos = butter(
        2,
        0.5,
        btype="highpass",
        fs=float(sample_rate),
        output="sos",
    )

    filtered = sosfiltfilt(
        sos,
        centered,
        axis=0,
    )

    return filtered.astype(np.float32)



  

def load_incart_buffer() -> IncartBuffer:
    record = wfdb.rdrecord(
        settings.INCART_RECORD,
        pn_dir=settings.INCART_PN_DIR,
        physical=True,
    )

    annotation = wfdb.rdann(
        settings.INCART_RECORD,
        settings.INCART_ANNOTATOR,
        pn_dir=settings.INCART_PN_DIR,
    )

    if record.p_signal is None:
        raise RuntimeError(
            "INCART returned no physical ECG signal."
        )

    available_leads = {
        normalize_lead_name(name): index
        for index, name in enumerate(record.sig_name)
    }

    selected_indexes: list[int] = []
    selected_ids: list[str] = []
    selected_names: list[str] = []

    for lead_id, expected_name in LEAD_CONFIG:
        normalized_name = normalize_lead_name(
            expected_name
        )

        if normalized_name not in available_leads:
            raise RuntimeError(
                f"INCART lead {expected_name} was not found. "
                f"Available leads: {record.sig_name}"
            )

        selected_indexes.append(
            available_leads[normalized_name]
        )
        selected_ids.append(lead_id)
        selected_names.append(expected_name)

    source_signals = np.asarray(
        record.p_signal[:, selected_indexes],
        dtype=np.float32,
    )

    source_sample_rate = float(record.fs)
    target_sample_rate = int(
        settings.WAVEFORM_SAMPLE_RATE
    )

    raw_signals_mv = linear_resample(
    source_signals,
    source_fs=source_sample_rate,
    target_fs=target_sample_rate,
)

    physical_signals_mv = center_ecg_for_display(
    raw_signals_mv,
    target_sample_rate,
)

    normalized_signals = normalize_for_webgl(
        physical_signals_mv
    )

    mapped_annotation_samples = np.rint(
        np.asarray(
            annotation.sample,
            dtype=np.float64,
        )
        * target_sample_rate
        / source_sample_rate
    ).astype(np.int64)

    annotation_symbols = np.asarray(
        annotation.symbol,
        dtype=object,
    )

    valid_annotations = (
        (mapped_annotation_samples >= 0)
        & (
            mapped_annotation_samples
            < physical_signals_mv.shape[0]
        )
    )

    mapped_annotation_samples = (
        mapped_annotation_samples[valid_annotations]
    )

    annotation_symbols = annotation_symbols[
        valid_annotations
    ]

    display_indexes = [
        selected_ids.index(lead_id)
        for lead_id in DISPLAY_LEADS
    ]

    lead_ii_index = selected_ids.index("lead2")

    estimated_hr = estimate_hr_from_lead_ii(
        physical_signals_mv[:, lead_ii_index],
        target_sample_rate,
    )

    print(
        "[KGEN INCART READY]",
        f"record={settings.INCART_RECORD}",
        f"source_fs={source_sample_rate}",
        f"target_fs={target_sample_rate}",
        f"samples={physical_signals_mv.shape[0]}",
        f"annotations={len(mapped_annotation_samples)}",
    )

    return IncartBuffer(
    sample_rate=target_sample_rate,
    source_sample_rate=source_sample_rate,
    record_name=settings.INCART_RECORD,
    pn_dir=settings.INCART_PN_DIR,
    raw_signals_mv=raw_signals_mv,
    physical_signals_mv=physical_signals_mv,
    normalized_signals=normalized_signals,
    lead_ids=selected_ids,
    lead_names=selected_names,
    display_indexes=display_indexes,
    annotation_samples=mapped_annotation_samples,
    annotation_symbols=annotation_symbols,
    estimated_hr=estimated_hr,
)

def get_incart_buffer() -> IncartBuffer:
    global _CACHED_BUFFER

    if _CACHED_BUFFER is not None:
        return _CACHED_BUFFER

    with _CACHE_LOCK:
        if _CACHED_BUFFER is None:
            _CACHED_BUFFER = load_incart_buffer()

    return _CACHED_BUFFER


async def build_incart_frame(
    *,
    cursor: int,
    batch_size: int,
) -> dict[str, Any]:
    buffer = _CACHED_BUFFER

    if buffer is None:
        buffer = await asyncio.to_thread(
            get_incart_buffer
        )

    (
        next_cursor,
        normalized_batch,
        physical_batch,
        annotations,
        loop_number,
    ) = buffer.read(
        cursor=cursor,
        batch_size=batch_size,
    )
    
    heart_rate = buffer.heart_rate_for_cursor(
    next_cursor
)
    lead_ids = DISPLAY_LEADS

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

    lead_name_map = {
        lead_id: buffer.lead_names[
            buffer.lead_ids.index(lead_id)
        ]
        for lead_id in lead_ids
    }

    return {
        "source": "physionet-incart",
        "record": buffer.record_name,
        "status": "connected",
        "receivedAt": now_iso(),
        "sampleRate": buffer.sample_rate,
        "sourceSampleRate": buffer.source_sample_rate,
        "batchSize": batch_size,
        "cursor": cursor,
        "nextCursor": next_cursor,
        "bufferSeconds": round(
            buffer.duration_seconds,
            3,
        ),
        "bufferSamples": buffer.total_samples,
        "loopNumber": loop_number,
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
        "leadNames": lead_name_map,
        "latestMv": latest_mv,
        "annotations": annotations,
        "availableLeadIds": buffer.lead_ids,
        "availableLeadNames": buffer.lead_names,
        "vitals": {
    "heartRate": heart_rate,
    "spo2": None,
    "ppgTrace": [],
    "systolic": None,
    "diastolic": None,
    "respiratoryRate": None,
    "temperature": None,
},
"displayProcessing": {
    "baselineCentered": True,
    "baselineMethod": "median removal plus 0.5 Hz display high-pass",
    "displayHighPassHz": 0.5,
    "rawPhysicalValuesPreserved": True,
},
"provenance": {
    "waveformSource": "PhysioNet INCART",
    "annotationSource": "INCART atr beat annotations",
    "clinicalContextSource": None,
},
    }
    
def get_incart_segment(
    *,
    start_sample: int,
    end_sample: int,
) -> dict[str, Any]:
    buffer = get_incart_buffer()

    start = max(0, int(start_sample))
    end = max(start + 1, int(end_sample))
    length = end - start

    indexes = (
        np.arange(length, dtype=np.int64) + start
    ) % buffer.total_samples

    centered_signals = buffer.physical_signals_mv[indexes]
    raw_signals = buffer.raw_signals_mv[indexes]

    annotations = buffer.annotations_for_window(
        start,
        length,
    )

    output_annotations = []

    for annotation in annotations:
        absolute_sample = int(annotation["absoluteSample"])

        output_annotations.append(
            {
                **annotation,
                "captureOffsetSamples": absolute_sample - start,
                "captureOffsetSeconds": round(
                    (absolute_sample - start)
                    / buffer.sample_rate,
                    3,
                ),
            }
        )

    return {
        "sampleRate": buffer.sample_rate,
        "sourceSampleRate": buffer.source_sample_rate,
        "record": buffer.record_name,
        "leadIds": list(buffer.lead_ids),
        "leadNames": list(buffer.lead_names),
        "centeredSignalsMv": centered_signals,
        "rawSignalsMv": raw_signals,
        "annotations": output_annotations,
    }