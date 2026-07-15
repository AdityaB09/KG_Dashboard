from __future__ import annotations

import asyncio
import json
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.config import settings
from app.incart_waveforms import get_incart_segment


@dataclass
class ActiveCapture:
    episode_id: str
    session_id: str
    catalog_entry: dict[str, Any]
    record: str
    loop_number: int
    loop_offset: int
    capture_start_abs: int
    event_start_abs: int
    event_end_abs: int
    capture_end_abs: int
    trigger_heart_rate: int | None
    ring_frame_count: int
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


class EpisodeCoordinator:
    def __init__(self) -> None:
        self.enabled = bool(settings.EPISODES_ENABLED)
        self.pre_seconds = float(
            settings.EPISODE_PRE_SECONDS
        )
        self.post_seconds = float(
            settings.EPISODE_POST_SECONDS
        )
        self.catalog_path = Path(
            settings.EPISODE_CATALOG_PATH
        )
        self.storage_path = Path(
            settings.EPISODE_STORAGE_PATH
        )
        self.storage_path.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.catalog = self._load_catalog()
        self.sessions: dict[str, SessionState] = {}
        self.subscribers: set[asyncio.Queue] = set()

    def _load_catalog(self) -> list[dict[str, Any]]:
        if not self.catalog_path.exists():
            return []

        try:
            content = json.loads(
                self.catalog_path.read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, json.JSONDecodeError):
            return []

        return content if isinstance(content, list) else []

    def _session(
        self,
        session_id: str,
    ) -> SessionState:
        if session_id not in self.sessions:
            self.sessions[session_id] = SessionState()

        return self.sessions[session_id]

    def _episode_id(
        self,
        entry: dict[str, Any],
        record: str,
        loop_number: int,
    ) -> str:
        catalog_id = str(
            entry.get("id")
            or f"incart-{record}-ep-unknown"
        )

        prefix = f"incart-{record}-"

        suffix = (
            catalog_id[len(prefix):]
            if catalog_id.startswith(prefix)
            else catalog_id
        )

        return (
            f"incart-{record}-loop-"
            f"{loop_number}-{suffix}"
        )

    def _episode_dir(
        self,
        episode_id: str,
    ) -> Path:
        return self.storage_path / episode_id

    def _episode_exists(
        self,
        episode_id: str,
    ) -> bool:
        return (
            self._episode_dir(episode_id)
            / "metadata.json"
        ).exists()

    def _publish(
        self,
        event: dict[str, Any],
    ) -> None:
        for queue in tuple(self.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def observe_frame(
        self,
        *,
        session_id: str,
        frame: dict[str, Any],
    ) -> None:
        if not self.enabled:
            return

        if frame.get("source") != "physionet-incart":
            return

        sample_rate = int(
            frame.get("sampleRate") or 0
        )
        buffer_samples = int(
            frame.get("bufferSamples") or 0
        )
        cursor = int(frame.get("cursor") or 0)
        next_cursor = int(
            frame.get("nextCursor")
            or cursor
        )
        loop_number = int(
            frame.get("loopNumber") or 1
        )
        record = str(frame.get("record") or "")

        if (
            sample_rate <= 0
            or buffer_samples <= 0
            or next_cursor <= cursor
        ):
            return

        state = self._session(session_id)

        state.frames.append(
            {
                "cursor": cursor,
                "nextCursor": next_cursor,
                "receivedAt": frame.get("receivedAt"),
                "leadsMv": frame.get("leadsMv") or {},
                "annotations": frame.get("annotations") or [],
            }
        )

        pre_samples = int(
            sample_rate * self.pre_seconds
        )
        cutoff = next_cursor - pre_samples

        while (
            state.frames
            and state.frames[0]["nextCursor"] < cutoff
        ):
            state.frames.popleft()

        loop_offset = (
            loop_number - 1
        ) * buffer_samples

        for entry in self.catalog:
            if str(entry.get("record")) != record:
                continue

            event_start_abs = loop_offset + int(
                round(
                    float(entry.get("startSeconds", 0))
                    * sample_rate
                )
            )

            event_end_abs = loop_offset + int(
                round(
                    float(
                        entry.get(
                            "endSeconds",
                            entry.get("startSeconds", 0),
                        )
                    )
                    * sample_rate
                )
            )

            if not (
                cursor
                <= event_start_abs
                < next_cursor
            ):
                continue

            episode_id = self._episode_id(
                entry,
                record,
                loop_number,
            )

            if (
                episode_id in state.active
                or self._episode_exists(episode_id)
            ):
                continue

            post_samples = int(
                sample_rate * self.post_seconds
            )

            capture_start_abs = max(
                loop_offset,
                event_start_abs - pre_samples,
            )

            capture_end_abs = min(
                loop_offset + buffer_samples,
                event_end_abs + post_samples,
            )

            heart_rate = (
                frame.get("vitals") or {}
            ).get("heartRate")

            capture = ActiveCapture(
                episode_id=episode_id,
                session_id=session_id,
                catalog_entry=dict(entry),
                record=record,
                loop_number=loop_number,
                loop_offset=loop_offset,
                capture_start_abs=capture_start_abs,
                event_start_abs=event_start_abs,
                event_end_abs=event_end_abs,
                capture_end_abs=capture_end_abs,
                trigger_heart_rate=(
                    int(heart_rate)
                    if heart_rate is not None
                    else None
                ),
                ring_frame_count=len(state.frames),
            )

            state.active[episode_id] = capture

            print(
                "[KGEN EPISODE DETECTED]",
                episode_id,
            )

            self._publish(
                {
                    "type": "episode.detected",
                    "episodeId": episode_id,
                    "patientId": f"research-incart-{record}",
                    "label": entry.get("display"),
                    "state": "CAPTURING_POST_EVENT",
                    "analysisStatus": "pending",
                }
            )

        for capture in list(
            state.active.values()
        ):
            if (
                capture.finalizing
                or next_cursor
                < capture.capture_end_abs
            ):
                continue

            capture.finalizing = True
            capture.state = "CAPTURED"

            try:
                asyncio.get_running_loop().create_task(
                    self._finalize_capture(capture)
                )
            except RuntimeError:
                capture.finalizing = False

    async def _finalize_capture(
        self,
        capture: ActiveCapture,
    ) -> None:
        try:
            segment = await asyncio.to_thread(
                get_incart_segment,
                start_sample=capture.capture_start_abs,
                end_sample=capture.capture_end_abs,
            )

            episode_dir = self._episode_dir(
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
                segment["centeredSignalsMv"],
                dtype=np.float32,
            )

            raw_signals = np.asarray(
                segment["rawSignalsMv"],
                dtype=np.float32,
            )

            lead_ids = list(segment["leadIds"])
            lead_names = list(segment["leadNames"])
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

            np.savez_compressed(
                episode_dir / "waveforms.npz",
                centered_mv=centered_signals,
                raw_mv=raw_signals,
                lead_ids=np.asarray(
                    lead_ids,
                    dtype="U16",
                ),
                lead_names=np.asarray(
                    lead_names,
                    dtype="U16",
                ),
                sample_rate=np.asarray(sample_rate),
                event_start_offset=np.asarray(
                    event_start_offset
                ),
                event_end_offset=np.asarray(
                    event_end_offset
                ),
            )

            entry = capture.catalog_entry

            annotation_symbols = sorted(
                {
                    str(item.get("symbol"))
                    for item in annotations
                    if item.get("symbol")
                }
            )

            metadata = {
                "id": capture.episode_id,
                "catalogId": entry.get("id"),
                "patientId": (
                    f"research-incart-"
                    f"{capture.record}"
                ),
                "record": capture.record,
                "loopNumber": capture.loop_number,
                "state": "CAPTURED",
                "analysisStatus": "pending",
                "label": entry.get(
                    "label",
                    "reviewed_annotation_window",
                ),
                "display": entry.get(
                    "display",
                    "Reviewed annotated rhythm window",
                ),
                "severity": entry.get(
                    "severity",
                    "warning",
                ),
                "sampleRate": sample_rate,
                "sourceSampleRate": segment.get(
                    "sourceSampleRate"
                ),
                "leadIds": lead_ids,
                "leadNames": lead_names,
                "captureStartSeconds": round(
                    (
                        capture.capture_start_abs
                        - capture.loop_offset
                    )
                    / sample_rate,
                    3,
                ),
                "captureEndSeconds": round(
                    (
                        capture.capture_end_abs
                        - capture.loop_offset
                    )
                    / sample_rate,
                    3,
                ),
                "eventStartSeconds": round(
                    (
                        capture.event_start_abs
                        - capture.loop_offset
                    )
                    / sample_rate,
                    3,
                ),
                "eventEndSeconds": round(
                    (
                        capture.event_end_abs
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
                        capture.event_end_abs
                        - capture.event_start_abs
                    )
                    / sample_rate,
                    3,
                ),
                "preSecondsCaptured": round(
                    (
                        capture.event_start_abs
                        - capture.capture_start_abs
                    )
                    / sample_rate,
                    3,
                ),
                "postSecondsCaptured": round(
                    (
                        capture.capture_end_abs
                        - capture.event_end_abs
                    )
                    / sample_rate,
                    3,
                ),
                "triggerHeartRate": (
                    capture.trigger_heart_rate
                ),
                "annotationCount": len(annotations),
                "annotationSymbols": annotation_symbols,
                "annotations": annotations,
                "ringBufferFramesAtTrigger": (
                    capture.ring_frame_count
                ),
                "diagnosis": {
                    "label": entry.get("label"),
                    "display": entry.get("display"),
                    "sourceType": entry.get(
                        "sourceType",
                        "dataset_annotation",
                    ),
                    "sourceName": (
                        "PhysioNet INCART"
                    ),
                    "confidence": None,
                    "referenceAnnotation": True,
                },
                "provenance": {
                    "waveformSource": (
                        "PhysioNet INCART"
                    ),
                    "annotationSource": (
                        "Reviewed INCART "
                        "annotation-derived window"
                    ),
                    "clinicalContextSource": None,
                },
                "capturedAt": datetime.now(
                    timezone.utc
                ).isoformat(),
            }

            self._write_json(
                episode_dir / "metadata.json",
                metadata,
            )

            self._write_json(
                episode_dir / "clinical_context.json",
                {
                    "status": "not_loaded",
                    "provenance": metadata[
                        "provenance"
                    ],
                },
            )

            self._write_json(
                episode_dir / "analysis.json",
                {
                    "status": "not_started",
                    "message": (
                        "Deterministic signal analysis "
                        "is added in the next phase."
                    ),
                },
            )

            print(
                "[KGEN EPISODE CAPTURED]",
                capture.episode_id,
            )

            self._publish(
                {
                    "type": "episode.captured",
                    "episodeId": capture.episode_id,
                    "patientId": metadata["patientId"],
                    "label": metadata["display"],
                    "state": "CAPTURED",
                    "analysisStatus": "pending",
                }
            )

        except Exception as error:
            print(
                "[KGEN EPISODE CAPTURE ERROR]",
                capture.episode_id,
                str(error),
            )

            self._publish(
                {
                    "type": "episode.error",
                    "episodeId": capture.episode_id,
                    "error": str(error),
                }
            )

        finally:
            session = self.sessions.get(
                capture.session_id
            )

            if session:
                session.active.pop(
                    capture.episode_id,
                    None,
                )

    def _write_json(
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

        for metadata_path in self.storage_path.glob(
            "*/metadata.json"
        ):
            try:
                episodes.append(
                    json.loads(
                        metadata_path.read_text(
                            encoding="utf-8"
                        )
                    )
                )
            except (OSError, json.JSONDecodeError):
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
        return episodes[0] if episodes else None

    def get_episode(
        self,
        episode_id: str,
    ) -> dict[str, Any]:
        path = (
            self._episode_dir(episode_id)
            / "metadata.json"
        )

        if not path.exists():
            raise FileNotFoundError(episode_id)

        return json.loads(
            path.read_text(encoding="utf-8")
        )

    def get_context(
        self,
        episode_id: str,
    ) -> dict[str, Any]:
        path = (
            self._episode_dir(episode_id)
            / "clinical_context.json"
        )

        if not path.exists():
            raise FileNotFoundError(episode_id)

        return json.loads(
            path.read_text(encoding="utf-8")
        )

    def get_analysis(
        self,
        episode_id: str,
    ) -> dict[str, Any]:
        path = (
            self._episode_dir(episode_id)
            / "analysis.json"
        )

        if not path.exists():
            raise FileNotFoundError(episode_id)

        return json.loads(
            path.read_text(encoding="utf-8")
        )

    def get_waveforms(
        self,
        episode_id: str,
        requested_leads: list[str] | None = None,
        max_points: int | None = None,
    ) -> dict[str, Any]:
        metadata = self.get_episode(episode_id)

        path = (
            self._episode_dir(episode_id)
            / "waveforms.npz"
        )

        if not path.exists():
            raise FileNotFoundError(episode_id)

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
                for value in data["lead_ids"].tolist()
            ]
            lead_names = [
                str(value)
                for value in data["lead_names"].tolist()
            ]
            sample_rate = int(
                np.asarray(
                    data["sample_rate"]
                ).item()
            )

        requested = requested_leads or [
            "lead2",
            "lead1",
            "avf",
        ]

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
                or settings.EPISODE_MAX_WAVEFORM_POINTS
            ),
        )

        step = max(
            1,
            int(
                math.ceil(
                    centered.shape[0] / limit
                )
            ),
        )

        sampled = centered[::step]

        leads_mv = {}

        for lead_id in selected:
            index = lead_ids.index(lead_id)

            leads_mv[lead_id] = [
                round(float(value), 5)
                for value in sampled[:, index]
            ]

        lead_name_map = {
            lead_id: lead_names[
                lead_ids.index(lead_id)
            ]
            for lead_id in selected
        }

        return {
            "episodeId": episode_id,
            "record": metadata.get("record"),
            "originalSampleRate": sample_rate,
            "sampleRate": round(
                sample_rate / step,
                3,
            ),
            "downsampleStep": step,
            "durationSeconds": metadata.get(
                "durationSeconds"
            ),
            "eventStartSeconds": metadata.get(
                "eventStartOffsetSeconds"
            ),
            "eventEndSeconds": metadata.get(
                "eventEndOffsetSeconds"
            ),
            "leadNames": lead_name_map,
            "leadsMv": leads_mv,
            "annotations": metadata.get(
                "annotations",
                [],
            ),
            "provenance": metadata.get(
                "provenance",
                {},
            ),
        }

    def subscribe(
        self,
    ) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(
            maxsize=100
        )
        self.subscribers.add(queue)
        return queue

    def unsubscribe(
        self,
        queue: asyncio.Queue,
    ) -> None:
        self.subscribers.discard(queue)


episode_coordinator = EpisodeCoordinator()