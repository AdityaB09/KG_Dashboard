from __future__ import annotations

import asyncio
import json
import math
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.config import settings
from app.incart_waveforms import (
    get_incart_buffer,
    get_incart_segment,
)
from app.incidents import incident_coordinator

def policy(
    category: str,
    label: str,
    display: str,
    mode: str,
    severity: str = "warning",
    interval_key: str | None = None,
) -> dict[str, Any]:
    return {
        "category": category,
        "label": label,
        "display": display,
        "mode": mode,
        "severity": severity,
        "intervalKey": interval_key,
    }


def normalize_annotation_symbol(
    value: Any,
) -> str:
    if isinstance(value, dict):
        value = (
            value.get("symbol")
            or value.get("code")
            or value.get("value")
            or ""
        )

    elif isinstance(value, (list, tuple)):
        value = value[0] if value else ""

    elif isinstance(value, set):
        value = next(iter(value), "")

    return str(value or "").strip()

ANNOTATION_POLICIES = {
    "N": policy(
        "normal_beat",
        "normal_beat",
        "Normal beat",
        "context",
        "info",
    ),
    "L": policy(
        "conduction_morphology",
        "left_bundle_branch_block_beat",
        "Left bundle branch block beat",
        "transition",
    ),
    "R": policy(
        "conduction_morphology",
        "right_bundle_branch_block_beat",
        "Right bundle branch block beat",
        "transition",
    ),
    "B": policy(
        "conduction_morphology",
        "bundle_branch_block_beat",
        "Bundle branch block beat",
        "transition",
    ),
    "A": policy(
        "supraventricular_ectopy",
        "atrial_premature_beat",
        "Atrial premature beat",
        "beat",
    ),
    "a": policy(
        "supraventricular_ectopy",
        "aberrated_atrial_premature_beat",
        "Aberrated atrial premature beat",
        "beat",
    ),
    "J": policy(
        "supraventricular_ectopy",
        "junctional_premature_beat",
        "Junctional premature beat",
        "beat",
    ),
    "S": policy(
        "supraventricular_ectopy",
        "supraventricular_ectopic_beat",
        "Supraventricular premature or ectopic beat",
        "beat",
    ),
    "V": policy(
        "ventricular_ectopy",
        "premature_ventricular_contraction",
        "Premature ventricular contraction",
        "beat",
    ),
    "r": policy(
        "ventricular_ectopy",
        "r_on_t_pvc",
        "R-on-T premature ventricular contraction",
        "beat",
        "critical",
    ),
    "F": policy(
        "ventricular_ectopy",
        "fusion_ventricular_normal_beat",
        "Fusion of ventricular and normal beat",
        "beat",
    ),
    "e": policy(
        "escape_beat",
        "atrial_escape_beat",
        "Atrial escape beat",
        "beat",
    ),
    "j": policy(
        "escape_beat",
        "junctional_escape_beat",
        "Junctional escape beat",
        "beat",
    ),
    "n": policy(
        "escape_beat",
        "supraventricular_escape_beat",
        "Supraventricular escape beat",
        "beat",
    ),
    "E": policy(
        "escape_beat",
        "ventricular_escape_beat",
        "Ventricular escape beat",
        "beat",
    ),
    "/": policy(
        "paced_morphology",
        "paced_beat",
        "Paced beat",
        "transition",
        "info",
    ),
    "f": policy(
        "paced_morphology",
        "fusion_paced_normal_beat",
        "Fusion of paced and normal beat",
        "beat",
    ),
    "Q": policy(
        "unclassified_beat",
        "unclassifiable_beat",
        "Unclassifiable beat",
        "beat",
    ),
    "?": policy(
        "unclassified_beat",
        "unclassified_learning_beat",
        "Beat not classified during learning",
        "beat",
    ),
    "[": policy(
        "ventricular_flutter_fibrillation",
        "ventricular_flutter_fibrillation",
        "Ventricular flutter or fibrillation",
        "interval_start",
        "critical",
        "vf",
    ),
    "!": policy(
        "ventricular_flutter_fibrillation",
        "ventricular_flutter_wave",
        "Ventricular flutter wave",
        "interval_continue",
        "critical",
        "vf",
    ),
    "]": policy(
        "ventricular_flutter_fibrillation",
        "ventricular_flutter_fibrillation_end",
        "End of ventricular flutter or fibrillation",
        "interval_end",
        "critical",
        "vf",
    ),
    "x": policy(
        "conduction_event",
        "non_conducted_p_wave",
        "Non-conducted P-wave",
        "event",
    ),
    "^": policy(
        "device_artifact",
        "noncaptured_pacemaker_artifact",
        "Non-captured pacemaker artifact",
        "event",
    ),
    "|": policy(
        "signal_quality",
        "qrs_like_artifact",
        "Isolated QRS-like artifact",
        "event",
    ),
    "~": policy(
        "signal_quality",
        "signal_quality_change",
        "Signal quality change",
        "event",
    ),
    "+": policy(
        "rhythm_change",
        "rhythm_change",
        "Rhythm change",
        "event",
    ),
    "s": policy(
        "st_t_change",
        "st_segment_change",
        "ST-segment change",
        "event",
    ),
    "T": policy(
        "st_t_change",
        "t_wave_change",
        "T-wave change",
        "event",
    ),
    "(": policy(
        "waveform_marker",
        "waveform_onset",
        "Waveform onset",
        "context",
        "info",
    ),
    ")": policy(
        "waveform_marker",
        "waveform_end",
        "Waveform end",
        "context",
        "info",
    ),
    "p": policy(
        "waveform_marker",
        "p_wave_peak",
        "P-wave peak",
        "context",
        "info",
    ),
    "t": policy(
        "waveform_marker",
        "t_wave_peak",
        "T-wave peak",
        "context",
        "info",
    ),
    "u": policy(
        "waveform_marker",
        "u_wave_peak",
        "U-wave peak",
        "context",
        "info",
    ),
    "`": policy(
        "waveform_marker",
        "pq_junction",
        "PQ junction",
        "context",
        "info",
    ),
    "'": policy(
        "waveform_marker",
        "j_point",
        "J-point",
        "context",
        "info",
    ),
    "*": policy(
        "physiology_marker",
        "systole",
        "Systole marker",
        "context",
        "info",
    ),
    "D": policy(
        "physiology_marker",
        "diastole",
        "Diastole marker",
        "context",
        "info",
    ),
    "=": policy(
        "measurement_marker",
        "measurement",
        "Measurement annotation",
        "context",
        "info",
    ),
    '"': policy(
        "note",
        "comment",
        "Comment annotation",
        "context",
        "info",
    ),
    "@": policy(
        "external_link",
        "external_data_link",
        "External data link",
        "context",
        "info",
    ),
}

UNKNOWN_POLICY = policy(
    "unknown_reference_annotation",
    "unknown_reference_annotation",
    "Unknown reference annotation",
    "event",
)

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

SEVERITY_ORDER = {
    "info": 0,
    "warning": 1,
    "critical": 2,
}


@dataclass
class ActiveCapture:
    episode_id: str
    session_id: str
    record: str
    loop_number: int
    loop_offset: int
    capture_start_abs: int
    event_start_abs: int
    event_end_abs: int
    capture_end_abs: int
    trigger_heart_rate: int | None
    ring_frame_count: int
    trigger_annotations: list[dict[str, Any]] = field(
        default_factory=list
    )
    open_interval_key: str | None = None
    state: str = "CAPTURING_POST_EVENT"
    finalizing: bool = False


@dataclass
class SessionState:
    frames: deque[dict[str, Any]] = field(
        default_factory=deque
    )
    active: dict[str, ActiveCapture] = field(
        default_factory=dict
    )
    last_beat_symbol: str | None = None
    last_trigger_abs_by_key: dict[str, int] = field(
        default_factory=dict
    )


class EpisodeCoordinator:
    def __init__(self) -> None:
        self.enabled = bool(
            settings.EPISODES_ENABLED
        )
        self.pre_seconds = float(
            settings.EPISODE_PRE_SECONDS
        )
        self.post_seconds = float(
            settings.EPISODE_POST_SECONDS
        )
        self.padding_seconds = float(
            settings.EPISODE_EVENT_PADDING_SECONDS
        )
        self.merge_gap_seconds = float(
            settings.EPISODE_MERGE_GAP_SECONDS
        )
        self.max_capture_seconds = float(
            settings.EPISODE_MAX_CAPTURE_SECONDS
        )
        self.persistent_cooldown_seconds = float(
            settings
            .EPISODE_PERSISTENT_COOLDOWN_SECONDS
        )

        self.storage_path = Path(
            settings.EPISODE_STORAGE_PATH
        )
        self.storage_path.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.sessions: dict[
            str,
            SessionState,
        ] = {}

        self.subscribers: set[
            asyncio.Queue
        ] = set()

        self.inflight_ids: set[str] = set()

    def annotation_policy(
    self,
    symbol: Any,
) -> dict[str, Any]:
        normalized_symbol = (
        normalize_annotation_symbol(symbol)
    )

        return dict(
        ANNOTATION_POLICIES.get(
            normalized_symbol,
            UNKNOWN_POLICY,
        )
    )

    def session(
        self,
        session_id: str,
    ) -> SessionState:
        if session_id not in self.sessions:
            self.sessions[session_id] = (
                SessionState()
            )

        return self.sessions[session_id]

    def episode_dir(
        self,
        episode_id: str,
    ) -> Path:
        return self.storage_path / episode_id

    def episode_exists(
        self,
        episode_id: str,
    ) -> bool:
        return (
            self.episode_dir(episode_id)
            / "metadata.json"
        ).exists()

    def safe_symbol(
        self,
        symbol: str,
    ) -> str:
        value = "".join(
            character
            if character.isalnum()
            else f"x{ord(character):02x}"
            for character in symbol
        )

        return value or "unknown"

    def episode_id(
        self,
        *,
        record: str,
        loop_number: int,
        symbol: str,
        event_sample: int,
    ) -> str:
        return (
            f"incart-{record}-"
            f"loop-{loop_number}-"
            f"{self.safe_symbol(symbol)}-"
            f"{event_sample:09d}"
        )

    def publish(
        self,
        event: dict[str, Any],
    ) -> None:
        for queue in tuple(self.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def event_from_annotation(
        self,
        annotation: dict[str, Any],
        *,
        policy_data: dict[str, Any],
        sample_rate: int,
        loop_offset: int,
    ) -> dict[str, Any]:
        absolute_sample = int(
            annotation.get("absoluteSample")
            or 0
        )

        display = str(
            policy_data["display"]
        )

        aux_note = str(
            annotation.get("auxNote")
            or ""
        ).strip()

        if (
    normalize_annotation_symbol(
        annotation.get("symbol")
    ) == "+"
    and aux_note
):
            display = (
                f"Rhythm change: "
                f"{aux_note.strip('()')}"
            )

        return {
            "symbol": normalize_annotation_symbol(
    annotation.get("symbol")
),
            "absoluteSample": absolute_sample,
            "recordSample": int(
                annotation.get("sample")
                if annotation.get("sample")
                is not None
                else absolute_sample
                - loop_offset
            ),
            "recordSeconds": round(
                (
                    absolute_sample
                    - loop_offset
                )
                / sample_rate,
                3,
            ),
            "auxNote": aux_note,
            "subtype": int(
                annotation.get("subtype")
                or 0
            ),
            "channel": int(
                annotation.get("channel")
                or 0
            ),
            "annotationNumber": int(
                annotation.get(
                    "annotationNumber"
                )
                or 0
            ),
            "category": policy_data[
                "category"
            ],
            "label": policy_data["label"],
            "display": display,
            "severity": policy_data[
                "severity"
            ],
            "mode": policy_data["mode"],
        }

    def capture_summary(
        self,
        capture: ActiveCapture,
    ) -> dict[str, Any]:
        symbol_counts = Counter(
            str(item.get("symbol") or "")
            for item
            in capture.trigger_annotations
        )

        category_counts = Counter(
            str(
                item.get("category")
                or "unknown"
            )
            for item
            in capture.trigger_annotations
        )

        displays: list[str] = []

        for item in capture.trigger_annotations:
            display = str(
                item.get("display")
                or "Reference annotation"
            )

            if display not in displays:
                displays.append(display)

        severity = max(
            (
                str(
                    item.get("severity")
                    or "info"
                )
                for item
                in capture.trigger_annotations
            ),
            key=lambda value: (
                SEVERITY_ORDER.get(value, 0)
            ),
            default="info",
        )

        if len(displays) == 1:
            display = displays[0]
        else:
            display = (
                "Mixed reference-annotation "
                "episode"
            )

        if len(
            capture.trigger_annotations
        ) > 1:
            display = (
                f"{display} "
                f"({len(capture.trigger_annotations)} "
                f"triggers)"
            )

        return {
            "display": display,
            "severity": severity,
            "symbolCounts": dict(
                symbol_counts
            ),
            "categoryCounts": dict(
                category_counts
            ),
            "displays": displays,
        }

    def transition_allowed(
        self,
        state: SessionState,
        *,
        symbol: str,
        event_sample: int,
        sample_rate: int,
    ) -> bool:
        key = f"transition:{symbol}"

        last_trigger = (
            state.last_trigger_abs_by_key.get(
                key
            )
        )

        cooldown = int(
            sample_rate
            * self.persistent_cooldown_seconds
        )

        if state.last_beat_symbol == symbol:
            return False

        if (
            last_trigger is not None
            and event_sample - last_trigger
            < cooldown
        ):
            return False

        state.last_trigger_abs_by_key[
            key
        ] = event_sample

        return True

    def event_allowed(
        self,
        state: SessionState,
        *,
        symbol: str,
        event_sample: int,
        sample_rate: int,
    ) -> bool:
        key = f"event:{symbol}"

        last_trigger = (
            state.last_trigger_abs_by_key.get(
                key
            )
        )

        cooldown = int(
            sample_rate
            * max(
                1,
                self.merge_gap_seconds,
            )
        )

        if (
            last_trigger is not None
            and event_sample - last_trigger
            < cooldown
        ):
            return False

        state.last_trigger_abs_by_key[
            key
        ] = event_sample

        return True

    def active_interval(
        self,
        state: SessionState,
        *,
        record: str,
        loop_number: int,
        interval_key: str,
    ) -> ActiveCapture | None:
        return next(
            (
                capture
                for capture
                in state.active.values()
                if (
                    not capture.finalizing
                    and capture.record
                    == record
                    and capture.loop_number
                    == loop_number
                    and capture.open_interval_key
                    == interval_key
                )
            ),
            None,
        )

    def merge_candidate(
        self,
        state: SessionState,
        *,
        record: str,
        loop_number: int,
        event_sample: int,
        merge_gap_samples: int,
    ) -> ActiveCapture | None:
        candidates = [
            capture
            for capture
            in state.active.values()
            if (
                not capture.finalizing
                and capture.record == record
                and capture.loop_number
                == loop_number
                and capture.open_interval_key
                is None
                and event_sample
                <= capture.capture_end_abs
                + merge_gap_samples
            )
        ]

        if not candidates:
            return None

        return max(
            candidates,
            key=lambda capture: (
                capture.capture_end_abs
            ),
        )

    def create_capture(
        self,
        *,
        state: SessionState,
        session_id: str,
        record: str,
        loop_number: int,
        loop_offset: int,
        loop_end: int,
        event_sample: int,
        event: dict[str, Any],
        sample_rate: int,
        trigger_heart_rate: int | None,
        interval_key: str | None = None,
    ) -> ActiveCapture:
        pre_samples = int(
            sample_rate * self.pre_seconds
        )

        post_samples = int(
            sample_rate * self.post_seconds
        )

        padding_samples = int(
            sample_rate * self.padding_seconds
        )

        event_start = max(
            loop_offset,
            event_sample - padding_samples,
        )

        event_end = min(
            loop_end,
            event_sample + padding_samples,
        )

        capture_start = max(
            loop_offset,
            event_start - pre_samples,
        )

        capture_end = min(
            loop_end,
            event_end + post_samples,
        )

        episode_id = self.episode_id(
            record=record,
            loop_number=loop_number,
            symbol=event["symbol"],
            event_sample=event_sample,
        )

        capture = ActiveCapture(
            episode_id=episode_id,
            session_id=session_id,
            record=record,
            loop_number=loop_number,
            loop_offset=loop_offset,
            capture_start_abs=capture_start,
            event_start_abs=event_start,
            event_end_abs=event_end,
            capture_end_abs=capture_end,
            trigger_heart_rate=(
                trigger_heart_rate
            ),
            ring_frame_count=len(
                state.frames
            ),
            trigger_annotations=[event],
            open_interval_key=interval_key,
        )

        state.active[episode_id] = capture
        self.inflight_ids.add(episode_id)

        summary = self.capture_summary(
            capture
        )

        print(
            "[KGEN EPISODE DETECTED]",
            episode_id,
            event["symbol"],
            event["recordSeconds"],
        )

        self.publish(
            {
                "type": "episode.detected",
                "episodeId": episode_id,
                "patientId": (
                    f"research-incart-"
                    f"{record}"
                ),
                "label": summary["display"],
                "state": capture.state,
                "analysisStatus": "pending",
            }
        )

        return capture

    def extend_capture(
        self,
        capture: ActiveCapture,
        *,
        event: dict[str, Any],
        event_sample: int,
        loop_end: int,
        sample_rate: int,
    ) -> None:
        padding_samples = int(
            sample_rate * self.padding_seconds
        )

        post_samples = int(
            sample_rate * self.post_seconds
        )

        max_capture_samples = int(
            sample_rate
            * self.max_capture_seconds
        )

        maximum_end = min(
            loop_end,
            capture.capture_start_abs
            + max_capture_samples,
        )

        capture.trigger_annotations.append(
            event
        )

        capture.event_end_abs = min(
            maximum_end,
            max(
                capture.event_end_abs,
                event_sample
                + padding_samples,
            ),
        )

        capture.capture_end_abs = min(
            maximum_end,
            capture.event_end_abs
            + post_samples,
        )

    def observe_frame(
        self,
        *,
        session_id: str,
        frame: dict[str, Any],
    ) -> None:
        if not self.enabled:
            return

        if (
            frame.get("source")
            != "physionet-incart"
        ):
            return

        sample_rate = int(
            frame.get("sampleRate")
            or 0
        )

        buffer_samples = int(
            frame.get("bufferSamples")
            or 0
        )

        cursor = int(
            frame.get("cursor")
            or 0
        )

        next_cursor = int(
            frame.get("nextCursor")
            or cursor
        )

        loop_number = int(
            frame.get("loopNumber")
            or 1
        )

        record = str(
            frame.get("record")
            or ""
        )

        if (
            sample_rate <= 0
            or buffer_samples <= 0
            or next_cursor <= cursor
        ):
            return

        state = self.session(session_id)

        state.frames.append(
            {
                "cursor": cursor,
                "nextCursor": next_cursor,
                "receivedAt": frame.get(
                    "receivedAt"
                ),
            }
        )

        ring_samples = int(
            sample_rate * self.pre_seconds
        )

        cutoff = (
            next_cursor - ring_samples
        )

        while (
            state.frames
            and state.frames[0][
                "nextCursor"
            ] < cutoff
        ):
            state.frames.popleft()

        loop_offset = (
            loop_number - 1
        ) * buffer_samples

        loop_end = (
            loop_offset + buffer_samples
        )

        merge_gap_samples = int(
            sample_rate
            * self.merge_gap_seconds
        )

        max_capture_samples = int(
            sample_rate
            * self.max_capture_seconds
        )

        heart_rate = (
            frame.get("vitals") or {}
        ).get("heartRate")

        trigger_heart_rate = (
            int(heart_rate)
            if heart_rate is not None
            else None
        )

        annotations = frame.get(
    "annotations"
) or []

        for annotation in annotations:
            if not isinstance(annotation, dict):
                continue

            symbol = normalize_annotation_symbol(
        annotation.get("symbol")
    )

            if not symbol:
                continue

            policy_data = (
                self.annotation_policy(
                    symbol
                )
            )

            mode = str(
                policy_data["mode"]
            )

            event_sample = int(
                annotation.get(
                    "absoluteSample"
                )
                or 0
            )

            if mode == "context":
                if (
    normalize_annotation_symbol(symbol)
    in BEAT_SYMBOLS
):
                    state.last_beat_symbol = (
                        symbol
                    )

                continue

            event = (
                self.event_from_annotation(
                    annotation,
                    policy_data=policy_data,
                    sample_rate=sample_rate,
                    loop_offset=loop_offset,
                )
            )

            if mode == "transition":
                allowed = (
                    self.transition_allowed(
                        state,
                        symbol=symbol,
                        event_sample=event_sample,
                        sample_rate=sample_rate,
                    )
                )

                state.last_beat_symbol = (
                    symbol
                )

                if not allowed:
                    continue

            elif symbol in BEAT_SYMBOLS:
                state.last_beat_symbol = (
                    symbol
                )

            if mode == "interval_start":
                interval_key = str(
                    policy_data.get(
                        "intervalKey"
                    )
                    or symbol
                )

                existing = (
                    self.active_interval(
                        state,
                        record=record,
                        loop_number=(
                            loop_number
                        ),
                        interval_key=(
                            interval_key
                        ),
                    )
                )

                if existing:
                    self.extend_capture(
                        existing,
                        event=event,
                        event_sample=(
                            event_sample
                        ),
                        loop_end=loop_end,
                        sample_rate=(
                            sample_rate
                        ),
                    )

                    continue

                candidate_id = (
                    self.episode_id(
                        record=record,
                        loop_number=(
                            loop_number
                        ),
                        symbol=symbol,
                        event_sample=(
                            event_sample
                        ),
                    )
                )

                if (
                    candidate_id
                    in self.inflight_ids
                    or self.episode_exists(
                        candidate_id
                    )
                ):
                    continue

                self.create_capture(
                    state=state,
                    session_id=session_id,
                    record=record,
                    loop_number=loop_number,
                    loop_offset=loop_offset,
                    loop_end=loop_end,
                    event_sample=event_sample,
                    event=event,
                    sample_rate=sample_rate,
                    trigger_heart_rate=(
                        trigger_heart_rate
                    ),
                    interval_key=interval_key,
                )

                continue

            if mode in {
                "interval_continue",
                "interval_end",
            }:
                interval_key = str(
                    policy_data.get(
                        "intervalKey"
                    )
                    or "interval"
                )

                existing = (
                    self.active_interval(
                        state,
                        record=record,
                        loop_number=(
                            loop_number
                        ),
                        interval_key=(
                            interval_key
                        ),
                    )
                )

                if existing:
                    self.extend_capture(
                        existing,
                        event=event,
                        event_sample=(
                            event_sample
                        ),
                        loop_end=loop_end,
                        sample_rate=(
                            sample_rate
                        ),
                    )

                    if (
                        mode
                        == "interval_end"
                    ):
                        existing.open_interval_key = (
                            None
                        )

                    continue

                if mode == "interval_end":
                    continue

            if (
                mode == "event"
                and not self.event_allowed(
                    state,
                    symbol=symbol,
                    event_sample=event_sample,
                    sample_rate=sample_rate,
                )
            ):
                continue

            existing = self.merge_candidate(
                state,
                record=record,
                loop_number=loop_number,
                event_sample=event_sample,
                merge_gap_samples=(
                    merge_gap_samples
                ),
            )

            if existing:
                self.extend_capture(
                    existing,
                    event=event,
                    event_sample=event_sample,
                    loop_end=loop_end,
                    sample_rate=sample_rate,
                )

                continue

            candidate_id = self.episode_id(
                record=record,
                loop_number=loop_number,
                symbol=symbol,
                event_sample=event_sample,
            )

            if (
                candidate_id
                in self.inflight_ids
                or self.episode_exists(
                    candidate_id
                )
            ):
                continue

            self.create_capture(
                state=state,
                session_id=session_id,
                record=record,
                loop_number=loop_number,
                loop_offset=loop_offset,
                loop_end=loop_end,
                event_sample=event_sample,
                event=event,
                sample_rate=sample_rate,
                trigger_heart_rate=(
                    trigger_heart_rate
                ),
            )

        for capture in list(
            state.active.values()
        ):
            hard_limit = min(
                loop_end,
                capture.capture_start_abs
                + max_capture_samples,
            )

            if (
                capture.open_interval_key
                and next_cursor
                >= hard_limit
            ):
                capture.open_interval_key = (
                    None
                )

                capture.capture_end_abs = (
                    hard_limit
                )

            if (
                capture.finalizing
                or capture.open_interval_key
                is not None
                or next_cursor
                < capture.capture_end_abs
            ):
                continue

            capture.finalizing = True
            capture.state = "CAPTURED"

            try:
                asyncio.get_running_loop().create_task(
                    self.finalize_capture(
                        capture
                    )
                )
            except RuntimeError:
                capture.finalizing = False

    async def finalize_capture(
        self,
        capture: ActiveCapture,
    ) -> None:
        try:
            segment = await asyncio.to_thread(
                get_incart_segment,
                start_sample=(
                    capture.capture_start_abs
                ),
                end_sample=(
                    capture.capture_end_abs
                ),
            )

            episode_dir = self.episode_dir(
                capture.episode_id
            )

            episode_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            sample_rate = int(
                segment["sampleRate"]
            )

            centered_signals = np.asarray(
                segment[
                    "centeredSignalsMv"
                ],
                dtype=np.float32,
            )

            raw_signals = np.asarray(
                segment["rawSignalsMv"],
                dtype=np.float32,
            )

            lead_ids = list(
                segment["leadIds"]
            )

            lead_names = list(
                segment["leadNames"]
            )

            annotations = list(
                segment["annotations"]
            )

            event_start_offset = (
                capture.event_start_abs
                - capture.capture_start_abs
            )

            event_end_offset = (
                capture.event_end_abs
                - capture.capture_start_abs
            )
            
            requested_pre_seconds = float(
    settings.EPISODE_PRE_SECONDS
)

            requested_post_seconds = float(
                settings.EPISODE_POST_SECONDS
            )

            actual_pre_seconds = (
                capture.event_start_abs
                - capture.capture_start_abs
            ) / sample_rate

            actual_post_seconds = (
                capture.capture_end_abs
                - capture.event_end_abs
            ) / sample_rate

            capture_duration_seconds = (
                len(centered_signals)
                / sample_rate
            )

            tolerance = 2 / sample_rate

            pre_context_complete = (
                actual_pre_seconds + tolerance
                >= requested_pre_seconds
            )

            post_context_complete = (
                actual_post_seconds + tolerance
                >= requested_post_seconds
            )

            capture_truncated_by_max = (
                capture_duration_seconds + tolerance
                >= float(
                    settings.EPISODE_MAX_CAPTURE_SECONDS
                )
                and not post_context_complete
            )

            truncation_reasons = []

            if not pre_context_complete:
                truncation_reasons.append(
                    "record_start_or_capture_boundary"
                )

            if not post_context_complete:
                truncation_reasons.append(
                    (
                        "max_capture_duration"
                        if capture_truncated_by_max
                        else "record_end_or_capture_boundary"
                    )
                )

            np.savez_compressed(
                episode_dir
                / "waveforms.npz",
                centered_mv=(
                    centered_signals
                ),
                raw_mv=raw_signals,
                lead_ids=np.asarray(
                    lead_ids,
                    dtype="U16",
                ),
                lead_names=np.asarray(
                    lead_names,
                    dtype="U16",
                ),
                sample_rate=np.asarray(
                    sample_rate
                ),
                event_start_offset=np.asarray(
                    event_start_offset
                ),
                event_end_offset=np.asarray(
                    event_end_offset
                ),
            )

            trigger_annotations = []

            for item in (
                capture.trigger_annotations
            ):
                absolute_sample = int(
                    item["absoluteSample"]
                )

                trigger_annotations.append(
                    {
                        **item,
                        "captureOffsetSamples": (
                            absolute_sample
                            - capture
                            .capture_start_abs
                        ),
                        "captureOffsetSeconds": round(
                            (
                                absolute_sample
                                - capture
                                .capture_start_abs
                            )
                            / sample_rate,
                            3,
                        ),
                    }
                )

            annotation_counts = Counter(
                str(
                    item.get("symbol")
                    or ""
                )
                for item in annotations
            )

            trigger_symbol_counts = Counter(
                str(
                    item.get("symbol")
                    or ""
                )
                for item
                in trigger_annotations
            )

            trigger_category_counts = Counter(
                str(
                    item.get("category")
                    or "unknown"
                )
                for item
                in trigger_annotations
            )

            abnormal_annotation_count = sum(
                count
                for symbol, count
                in annotation_counts.items()
                if self.annotation_policy(
                    symbol
                )["mode"]
                != "context"
            )

            signal_quality_count = sum(
                count
                for symbol, count
                in annotation_counts.items()
                if self.annotation_policy(
                    symbol
                )["category"]
                == "signal_quality"
            )

            event_annotations = [
                item
                for item in annotations
                if (
                    event_start_offset
                    <= int(
                        item.get(
                            "captureOffsetSamples",
                            -1,
                        )
                    )
                    <= event_end_offset
                )
            ]

            event_annotation_counts = (
                Counter(
                    str(
                        item.get("symbol")
                        or ""
                    )
                    for item
                    in event_annotations
                )
            )

            summary = self.capture_summary(
                capture
            )

            metadata = {
                "id": capture.episode_id,
                "schemaVersion": "episode-v2",
                "patientId": (
                    f"research-incart-"
                    f"{capture.record}"
                ),
                "record": capture.record,
                "loopNumber": (
                    capture.loop_number
                ),
                "state": "CAPTURED",
                "analysisStatus": "pending",
                "autoTriggered": True,
                "triggerSource": (
                    "incart_reference_annotations"
                ),
                "policyVersion": (
                    "wfdb-reference-v1"
                ),
                "label": (
                    "reference_annotation_episode"
                ),
                "display": summary[
                    "display"
                ],
                "severity": summary[
                    "severity"
                ],
                "sampleRate": sample_rate,
                "sourceSampleRate": (
                    segment.get(
                        "sourceSampleRate"
                    )
                ),
                "leadIds": lead_ids,
                "leadNames": lead_names,
                "captureStartSeconds": round(
                    (
                        capture
                        .capture_start_abs
                        - capture.loop_offset
                    )
                    / sample_rate,
                    3,
                ),
                "captureEndSeconds": round(
                    (
                        capture
                        .capture_end_abs
                        - capture.loop_offset
                    )
                    / sample_rate,
                    3,
                ),
                "eventStartSeconds": round(
                    (
                        capture
                        .event_start_abs
                        - capture.loop_offset
                    )
                    / sample_rate,
                    3,
                ),
                "eventEndSeconds": round(
                    (
                        capture
                        .event_end_abs
                        - capture.loop_offset
                    )
                    / sample_rate,
                    3,
                ),
                "eventStartOffsetSeconds": round(
                    event_start_offset
                    / sample_rate,
                    3,
                ),
                "eventEndOffsetSeconds": round(
                    event_end_offset
                    / sample_rate,
                    3,
                ),
                "durationSeconds": round(
                    len(centered_signals)
                    / sample_rate,
                    3,
                ),
                "eventDurationSeconds": round(
                    (
                        capture
                        .event_end_abs
                        - capture
                        .event_start_abs
                    )
                    / sample_rate,
                    3,
                ),
                "preSecondsCaptured": round(
                    (
                        capture
                        .event_start_abs
                        - capture
                        .capture_start_abs
                    )
                    / sample_rate,
                    3,
                ),
                "postSecondsCaptured": round(
                    (
                        capture
                        .capture_end_abs
                        - capture
                        .event_end_abs
                    )
                    / sample_rate,
                    3,
                ),
                "captureCompleteness": {
    "requestedPreSeconds": round(
        requested_pre_seconds,
        3,
    ),
    "actualPreSeconds": round(
        actual_pre_seconds,
        3,
    ),
    "preContextComplete": (
        pre_context_complete
    ),
    "requestedPostSeconds": round(
        requested_post_seconds,
        3,
    ),
    "actualPostSeconds": round(
        actual_post_seconds,
        3,
    ),
    "postContextComplete": (
        post_context_complete
    ),
    "captureComplete": (
        pre_context_complete
        and post_context_complete
    ),
    "captureTruncatedByMaxDuration": (
        capture_truncated_by_max
    ),
    "truncationReasons": (
        truncation_reasons
    ),
},
                "triggerHeartRate": (
                    capture
                    .trigger_heart_rate
                ),
                "annotationCount": len(
                    annotations
                ),
                "annotationCounts": dict(
                    annotation_counts
                ),
                "normalAnnotationCount": (
                    annotation_counts.get(
                        "N",
                        0,
                    )
                ),
                "abnormalAnnotationCount": (
                    abnormal_annotation_count
                ),
                "signalQualityAnnotationCount": (
                    signal_quality_count
                ),
                "eventAnnotationCount": len(
                    event_annotations
                ),
                "eventAnnotationCounts": dict(
                    event_annotation_counts
                ),
                "triggerAnnotationCount": len(
                    trigger_annotations
                ),
                "triggerAnnotationCounts": dict(
                    trigger_symbol_counts
                ),
                "triggerCategoryCounts": dict(
                    trigger_category_counts
                ),
                "triggerAnnotations": (
                    trigger_annotations
                ),
                "annotations": annotations,
                "ringBufferFramesAtTrigger": (
                    capture
                    .ring_frame_count
                ),
                "referenceFinding": {
                    "display": summary[
                        "display"
                    ],
                    "severity": summary[
                        "severity"
                    ],
                    "triggerSymbolCounts": dict(
                        trigger_symbol_counts
                    ),
                    "triggerCategoryCounts": dict(
                        trigger_category_counts
                    ),
                    "triggerDisplays": summary[
                        "displays"
                    ],
                    "sourceType": (
                        "dataset_reference_annotation"
                    ),
                    "sourceName": (
                        "PhysioNet INCART"
                    ),
                    "referenceAnnotation": True,
                },
                "diagnosis": None,
                "provenance": {
                    "waveformSource": (
                        "PhysioNet INCART"
                    ),
                    "annotationSource": (
                        "INCART atr "
                        "reference annotations"
                    ),
                    "clinicalContextSource": None,
                },
                "capturedAt": (
                    datetime.now(
                        timezone.utc
                    ).isoformat()
                ),
            }
            if settings.INCIDENTS_ENABLED:
                 incident = (
        incident_coordinator
        .register_episode(metadata)
    )

            metadata["incidentId"] = (
                incident["id"]
            )

            metadata[
                "incidentPrimaryEpisodeId"
            ] = incident.get(
                "primaryEpisodeId"
            )

            metadata[
                "incidentBestContextEpisodeId"
            ] = incident.get(
                "bestContextEpisodeId"
            )
            self.write_json(
                episode_dir
                / "metadata.json",
                metadata,
            )
            
            self.write_json(
                episode_dir
                / "clinical_context.json",
                {
                    "status": "not_loaded",
                    "provenance": metadata[
                        "provenance"
                    ],
                },
            )

            self.write_json(
                episode_dir
                / "analysis.json",
                {
                    "status": "not_started",
                    "message": (
                        "Deterministic signal "
                        "analysis is pending."
                    ),
                },
            )

            print(
                "[KGEN EPISODE CAPTURED]",
                capture.episode_id,
            )

            self.publish(
                {
                    "type": (
                        "episode.captured"
                    ),
                    "incidentId": metadata.get(
    "incidentId"
),
                    "episodeId": (
                        capture.episode_id
                    ),
                    "patientId": metadata[
                        "patientId"
                    ],
                    "label": metadata[
                        "display"
                    ],
                    "state": "CAPTURED",
                    "analysisStatus": (
                        "pending"
                    ),
                }
            )
            try:
                    # Evaluation-injection V7 runs its Etiology Engine directly.
                    # Keep the legacy Phase 7 scheduler for every other capture mode.
                    if str(metadata.get("mode") or "") != "evaluation_injection":
                        from app.phase7.orchestrator import (
                            phase7_orchestrator,
                        )

                        phase7_orchestrator.schedule_captured_episode(
                            episode_id=capture.episode_id,
                            incident_id=metadata.get(
                                "incidentId"
                            ),
                        )

            except Exception as phase7_error:
                    # Scheduling failure must never break
                    # waveform capture or persistence.
                    print(
                        "[KGEN PHASE7 SCHEDULE ERROR]",
                        type(phase7_error).__name__,
                        str(phase7_error),
                    )
            
        
        except Exception as error:
            print(
                "[KGEN EPISODE "
                "CAPTURE ERROR]",
                capture.episode_id,
                str(error),
            )

            self.publish(
                {
                    "type": "episode.error",
                    "episodeId": (
                        capture.episode_id
                    ),
                    "error": str(error),
                }
            )

        finally:
            self.inflight_ids.discard(
                capture.episode_id
            )

            session = self.sessions.get(
                capture.session_id
            )

            if session:
                session.active.pop(
                    capture.episode_id,
                    None,
                )

    def write_json(
        self,
        path: Path,
        content: dict[str, Any],
    ) -> None:
        path.write_text(
            json.dumps(
                content,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def list_episodes(
        self,
    ) -> list[dict[str, Any]]:
        episodes = []

        for path in self.storage_path.glob(
            "*/metadata.json"
        ):
            try:
                episodes.append(
                    json.loads(
                        path.read_text(
                            encoding="utf-8"
                        )
                    )
                )
            except (
                OSError,
                json.JSONDecodeError,
            ):
                continue

        return sorted(
            episodes,
            key=lambda item: item.get(
                "capturedAt",
                "",
            ),
            reverse=True,
        )

    def get_latest_episode(
        self,
    ) -> dict[str, Any] | None:
        episodes = self.list_episodes()

        return (
            episodes[0]
            if episodes
            else None
        )

    def get_episode(
        self,
        episode_id: str,
    ) -> dict[str, Any]:
        path = (
            self.episode_dir(episode_id)
            / "metadata.json"
        )

        if not path.exists():
            raise FileNotFoundError(
                episode_id
            )

        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    def get_context(
        self,
        episode_id: str,
    ) -> dict[str, Any]:
        path = (
            self.episode_dir(episode_id)
            / "clinical_context.json"
        )

        if not path.exists():
            raise FileNotFoundError(
                episode_id
            )

        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    def get_analysis(
        self,
        episode_id: str,
    ) -> dict[str, Any]:
        path = (
            self.episode_dir(episode_id)
            / "analysis.json"
        )

        if not path.exists():
            raise FileNotFoundError(
                episode_id
            )

        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    def get_annotation_summary(
        self,
    ) -> dict[str, Any]:
        buffer = get_incart_buffer()

        symbols = [
            str(value)
            for value
            in buffer
            .annotation_symbols
            .tolist()
        ]

        counts = Counter(symbols)
        classified = {}

        for symbol, count in sorted(
            counts.items()
        ):
            policy_data = (
                self.annotation_policy(
                    symbol
                )
            )

            classified[symbol] = {
                "count": count,
                "category": policy_data[
                    "category"
                ],
                "display": policy_data[
                    "display"
                ],
                "severity": policy_data[
                    "severity"
                ],
                "mode": policy_data[
                    "mode"
                ],
                "triggersEpisode": (
                    policy_data["mode"]
                    != "context"
                ),
            }

        return {
            "record": buffer.record_name,
            "durationSeconds": (
                buffer.duration_seconds
            ),
            "sampleRate": (
                buffer.sample_rate
            ),
            "totalAnnotations": len(
                symbols
            ),
            "symbols": classified,
        }

    def get_waveforms(
        self,
        episode_id: str,
        requested_leads: (
            list[str] | None
        ) = None,
        max_points: int | None = None,
    ) -> dict[str, Any]:
        metadata = self.get_episode(
            episode_id
        )

        path = (
            self.episode_dir(episode_id)
            / "waveforms.npz"
        )

        if not path.exists():
            raise FileNotFoundError(
                episode_id
            )

        with np.load(
            path,
            allow_pickle=False,
        ) as data:
            centered = np.asarray(
                data["centered_mv"],
                dtype=np.float32,
            )

            lead_ids = [
                str(value)
                for value
                in data[
                    "lead_ids"
                ].tolist()
            ]

            lead_names = [
                str(value)
                for value
                in data[
                    "lead_names"
                ].tolist()
            ]

            sample_rate = int(
                np.asarray(
                    data["sample_rate"]
                ).item()
            )

        requested = (
            requested_leads
            or [
                "lead2",
                "lead1",
                "avf",
            ]
        )

        selected = [
            lead_id
            for lead_id in requested
            if lead_id in lead_ids
        ]

        if not selected:
            selected = lead_ids[:3]

        limit = max(
            100,
            int(
                max_points
                or settings
                .EPISODE_MAX_WAVEFORM_POINTS
            ),
        )

        step = max(
            1,
            int(
                math.ceil(
                    centered.shape[0]
                    / limit
                )
            ),
        )

        sampled = centered[::step]

        leads_mv = {}

        for lead_id in selected:
            index = lead_ids.index(
                lead_id
            )

            leads_mv[lead_id] = [
                round(float(value), 5)
                for value
                in sampled[:, index]
            ]

        lead_name_map = {
            lead_id: lead_names[
                lead_ids.index(lead_id)
            ]
            for lead_id in selected
        }

        return {
            "episodeId": episode_id,
            "record": metadata.get(
                "record"
            ),
            "originalSampleRate": (
                sample_rate
            ),
            "sampleRate": round(
                sample_rate / step,
                3,
            ),
            "downsampleStep": step,
            "durationSeconds": (
                metadata.get(
                    "durationSeconds"
                )
            ),
            "eventStartSeconds": (
                metadata.get(
                    "eventStartOffsetSeconds"
                )
            ),
            "eventEndSeconds": (
                metadata.get(
                    "eventEndOffsetSeconds"
                )
            ),
            "leadNames": lead_name_map,
            "leadsMv": leads_mv,
            "annotations": metadata.get(
                "annotations",
                [],
            ),
            "triggerAnnotations": (
                metadata.get(
                    "triggerAnnotations",
                    [],
                )
            ),
            "provenance": metadata.get(
                "provenance",
                {},
            ),
        }

    def subscribe(
        self,
    ) -> asyncio.Queue:
        queue: asyncio.Queue = (
            asyncio.Queue(
                maxsize=100
            )
        )

        self.subscribers.add(queue)

        return queue

    def unsubscribe(
        self,
        queue: asyncio.Queue,
    ) -> None:
        self.subscribers.discard(queue)


episode_coordinator = EpisodeCoordinator()