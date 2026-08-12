from __future__ import annotations

import asyncio
import json
import math
import os
import re
import secrets
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

import numpy as np
from app.evaluation_injection.etiology_v7 import run_etiology_v7
from app.analysis.episode_analyzer import episode_analyzer
from app.analysis.incident_analyzer import incident_analyzer
from app.clinical_context import clinical_context_service
from app.config import settings
from app.episodes import episode_coordinator
from app.incidents import incident_coordinator
from app.evaluation_injection.scenario_catalog import (
    detected_annotation_details,
    detector_policy,
)


DISPLAY_LEADS = [
    "lead1",
    "lead2",
    "lead3",
    "avr",
    "avl",
    "avf",
]

LEAD_NAME_MAP = {
    "I": "lead1",
    "II": "lead2",
    "III": "lead3",
    "aVR": "avr",
    "AVR": "avr",
    "aVL": "avl",
    "AVL": "avl",
    "aVF": "avf",
    "AVF": "avf",
}

OUTPUT_LEAD_NAMES = [
    "I",
    "II",
    "III",
    "aVR",
    "aVL",
    "aVF",
]


SUPPORTED_EVALUATION_BASE_SOURCES = {
    "physionet-incart",
    "api-range",
}

API_RANGE_CAPTURE_HEADROOM_SECONDS = 0.0

API_RANGE_CAPTURE_MODE = (
    os.getenv(
        "EVALUATION_API_RANGE_CAPTURE_MODE",
        "repeat_snapshot",
    )
    .strip()
    .lower()
    .replace("-", "_")
)

if API_RANGE_CAPTURE_MODE not in {
    "repeat_snapshot",
    "continuous",
}:
    API_RANGE_CAPTURE_MODE = (
        "repeat_snapshot"
    )


def normalize_waveform_source(
    value: Any,
) -> str:
    """Normalize UI/backend waveform source aliases to one canonical form."""
    return (
        str(value or "")
        .strip()
        .lower()
        .replace("_", "-")
    )


def is_api_range_source(
    value: Any,
) -> bool:
    """Return True for both api_range and api-range source spellings."""
    return (
        normalize_waveform_source(
            value
        )
        == "api-range"
    )


def environment_float(
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(
            os.getenv(
                name,
                str(default),
            )
        )
    except (
        TypeError,
        ValueError,
    ):
        value = default

    return max(
        minimum,
        min(maximum, value),
    )


LEGACY_INJECTION_TRANSITION_SECONDS = (
    environment_float(
        "EVALUATION_INJECTION_TRANSITION_SECONDS",
        0.10,
        0.02,
        0.25,
    )
)

INJECTION_TRANSITION_SECONDS = (
    environment_float(
        "EVALUATION_INJECTION_TRANSITION_SECONDS",
        LEGACY_INJECTION_TRANSITION_SECONDS,
        0.02,
        0.25,
    )
)

INJECTION_MIN_SIGNAL_SCALE_MV = (
    environment_float(
        "EVALUATION_INJECTION_MIN_SIGNAL_SCALE_MV",
        0.08,
        0.01,
        1.00,
    )
)

INJECTION_MAX_SIGNAL_SCALE_MV = (
    environment_float(
        "EVALUATION_INJECTION_MAX_SIGNAL_SCALE_MV",
        4.00,
        0.50,
        20.00,
    )
)


def robust_center_scale(
    values: Any,
) -> tuple[float, float]:
    array = np.asarray(
        values,
        dtype=np.float64,
    )
    array = array[
        np.isfinite(array)
    ]

    if not array.size:
        return (
            0.0,
            INJECTION_MIN_SIGNAL_SCALE_MV,
        )

    center = float(
        np.median(array)
    )
    centered = array - center
    scale = float(
        np.quantile(
            np.abs(centered),
            0.995,
        )
        or 0.0
    )

    if not np.isfinite(scale):
        scale = 0.0

    scale = float(
        np.clip(
            scale,
            INJECTION_MIN_SIGNAL_SCALE_MV,
            INJECTION_MAX_SIGNAL_SCALE_MV,
        )
    )

    return center, scale


def cubic_decay_correction(
    progress: float,
    value_delta: float,
    slope_delta: float,
    span_samples: int,
) -> float:
    """Decay an endpoint value/slope correction to zero with cubic Hermite."""
    t = float(np.clip(progress, 0.0, 1.0))
    t2 = t * t
    t3 = t2 * t
    h00 = 2.0 * t3 - 3.0 * t2 + 1.0
    h10 = t3 - 2.0 * t2 + t
    return float(
        h00 * value_delta
        + h10 * slope_delta * max(int(span_samples), 1)
    )


def transition_sample_count(
    sample_rate: int | float,
) -> int:
    return max(
        1,
        int(
            round(
                max(float(sample_rate), 1.0)
                * INJECTION_TRANSITION_SECONDS
            )
        ),
    )



def now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def safe_slug(
    value: str,
) -> str:
    text = re.sub(
        r"[^A-Za-z0-9]+",
        "-",
        str(value or ""),
    ).strip("-")

    return text or "scenario"


def atomic_json(
    path: Path,
    value: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_suffix(
        path.suffix + ".tmp"
    )

    temporary.write_text(
        json.dumps(
            value,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    temporary.replace(path)


def finite_number(
    value: Any,
    default: float | None = None,
) -> float | None:
    try:
        numeric = float(value)
    except (
        TypeError,
        ValueError,
    ):
        return default

    return (
        numeric
        if math.isfinite(numeric)
        else default
    )


def value_and_unit(
    entry: Any,
) -> tuple[
    float | str | None,
    str | None,
]:
    if isinstance(entry, dict):
        return (
            entry.get("value"),
            entry.get("unit"),
        )

    return entry, None


@dataclass
class LoadedScenario:
    scenario_id: str
    display: str
    severity: str
    patient: dict[str, Any]
    episode: dict[str, Any]
    vitals: dict[str, Any]
    labs: dict[str, Any]
    medications: Any
    clinical_context: dict[str, Any]
    ecg_measurements: dict[str, Any]
    sample_rate: int
    duration_seconds: float
    waveforms_mv: dict[
        str,
        np.ndarray,
    ]
    normalized_waveforms: dict[
        str,
        np.ndarray,
    ]
    baseline_offsets_mv: dict[
        str,
        float,
    ]
    trigger_heart_rate: int | None
    qrs_duration_ms: float | None


@dataclass
class InjectionSession:
    session_id: str
    scenario: LoadedScenario
    baseline_seconds: float
    pre_seconds: float
    post_seconds: float
    run_slm: bool
    state: str = "ARMED"
    armed_at: str = field(
        default_factory=now_iso
    )
    updated_at: str = field(
        default_factory=now_iso
    )
    sample_rate: int | None = None
    baseline_samples_seen: int = 0
    global_samples_seen: int = 0
    scenario_cursor: int = 0
    post_samples_seen: int = 0
    reference_onset_abs: int | None = None
    reference_end_abs: int | None = None
    detected_trigger_abs: int | None = None
    detector_rule_id: str | None = None
    detector_rate_bpm: float | None = None
    pre_buffers: dict[
        str,
        deque[float],
    ] = field(
        default_factory=dict
    )
    capture_buffers: dict[
        str,
        list[float],
    ] = field(
        default_factory=dict
    )
    detector_buffers: dict[
        str,
        list[float],
    ] = field(
        default_factory=dict
    )
    episode_id: str | None = None
    incident_id: str | None = None
    error: str | None = None
    analysis_result: dict[
        str,
        Any,
    ] | None = None
    score: dict[
        str,
        Any,
    ] | None = None
    final_task_started: bool = False
    # Sanitized Oracle demo binding. Never place access tokens in this object.
    oracle_demo: dict[str, Any] | None = None
    # Separate Epic demo binding. Kept distinct from Oracle to prevent provider mixups.
    epic_demo: dict[str, Any] | None = None
    # Compatibility field only. Episode-pack-only evaluation never uses it.
    token_override: dict[str, Any] | None = field(default=None, repr=False)
    base_waveform_source: str | None = None
    base_waveform_record: str | None = None
    base_source_sample_rate: float | None = None
    base_buffer_samples: int | None = None
    base_buffer_seconds: float | None = None
    base_cursor_start: int | None = None
    base_cursor_end: int | None = None
    base_source_wrapped: bool = False
    base_source_wrap_count: int = 0
    base_source_replayed: bool = False
    base_source_frames_seen: int = 0
    api_range_capture_mode: str = (
        API_RANGE_CAPTURE_MODE
    )
    injection_waveforms_mv: dict[
        str,
        np.ndarray,
    ] = field(
        default_factory=dict
    )
    injection_display_centers_mv: dict[
        str,
        float,
    ] = field(
        default_factory=dict
    )
    injection_display_scales_mv: dict[
        str,
        float,
    ] = field(
        default_factory=dict
    )
    injection_calibration: dict[
        str,
        dict[str, float],
    ] = field(
        default_factory=dict
    )
    injection_entry_value_mv: dict[
        str,
        float,
    ] = field(default_factory=dict)
    injection_entry_slope_mv: dict[
        str,
        float,
    ] = field(default_factory=dict)
    injection_last_value_mv: dict[
        str,
        float,
    ] = field(default_factory=dict)
    injection_last_slope_mv: dict[
        str,
        float,
    ] = field(default_factory=dict)
    post_api_start_value_mv: dict[
        str,
        float,
    ] = field(default_factory=dict)
    post_api_start_slope_mv: dict[
        str,
        float,
    ] = field(default_factory=dict)
    post_transition_samples_seen: int = 0

    def public_status(
        self,
    ) -> dict[str, Any]:
        rate = max(
            int(
                self.sample_rate
                or settings
                .WAVEFORM_SAMPLE_RATE
            ),
            1,
        )

        baseline_target = int(
            rate
            * self.baseline_seconds
        )

        scenario_total = max(
            1,
            min(
                len(
                    self.scenario
                    .waveforms_mv[
                        lead_id
                    ]
                )
                for lead_id
                in DISPLAY_LEADS
            ),
        )

        post_target = int(
            rate
            * self.post_seconds
        )

        if self.state == "ARMED":
            remaining = max(
                0,
                baseline_target
                - self.baseline_samples_seen,
            ) / rate

        elif self.state == "INJECTING":
            remaining = max(
                0,
                scenario_total
                - self.scenario_cursor,
            ) / rate

        elif self.state == "POST_EVENT":
            remaining = max(
                0,
                post_target
                - self.post_samples_seen,
            ) / rate

        else:
            remaining = 0.0

        trigger_latency = None

        if (
            self.reference_onset_abs
            is not None
            and self.detected_trigger_abs
            is not None
        ):
            trigger_latency = (
                self.detected_trigger_abs
                - self.reference_onset_abs
            ) / rate

        return {
            "enabled": True,
            "sessionId": (
                self.session_id
            ),
            "scenarioId": (
                self.scenario
                .scenario_id
            ),
            "scenarioDisplay": (
                self.scenario
                .display
            ),
            "state": self.state,
            "remainingSeconds": round(
                remaining,
                2,
            ),
            "baselineSeconds": (
                self.baseline_seconds
            ),
            "preSeconds": (
                self.pre_seconds
            ),
            "eventSeconds": (
                self.scenario
                .duration_seconds
            ),
            "postSeconds": (
                self.post_seconds
            ),
            "runSlm": self.run_slm,
            "contextMode": (
                "episode_pack_only"
            ),
            "episodePackPatient": {
                key: value
                for key, value in (
                    self.scenario.patient
                    or {}
                ).items()
                if key != "disclaimer"
            },
            "referenceOnsetSample": (
                self.reference_onset_abs
            ),
            "detectedTriggerSample": (
                self.detected_trigger_abs
            ),
            "triggerLatencySeconds": (
                round(
                    trigger_latency,
                    3,
                )
                if trigger_latency
                is not None
                else None
            ),
            "detectorRuleId": (
                self.detector_rule_id
            ),
            "triggerPolicyMode": detector_policy(
                self.scenario.scenario_id
            ).get("mode"),
            "detectorRateBpm": (
                round(
                    self.detector_rate_bpm,
                    1,
                )
                if self.detector_rate_bpm
                is not None
                else None
            ),
            "waveformBaselineMv": 0.0,
            "sourceBaselineOffsetsMv": (
                self.scenario
                .baseline_offsets_mv
            ),
            "episodeId": (
                self.episode_id
            ),
            "incidentId": (
                self.incident_id
            ),
            "error": self.error,
            "baseWaveformSource": (
                self.base_waveform_source
            ),
            "baseWaveformRecord": (
                self.base_waveform_record
            ),
            "baseCursorStart": (
                self.base_cursor_start
            ),
            "baseCursorEnd": (
                self.base_cursor_end
            ),
            "baseSourceWrapped": (
                self.base_source_wrapped
            ),
            "baseSourceWrapCount": (
                self.base_source_wrap_count
            ),
            "baseSourceReplayed": (
                self.base_source_replayed
            ),
            "apiRangeCaptureMode": (
                self.api_range_capture_mode
            ),
            "waveformBlend": {
                "method": (
                    "endpoint-constrained-hermite-v2"
                ),
                "transitionSeconds": (
                    INJECTION_TRANSITION_SECONDS
                ),
                "displayMatchesPersistedMv": True,
                "calibrated": bool(
                    self.injection_waveforms_mv
                ),
            },
            "updatedAt": (
                self.updated_at
            ),
            "oracleDemo": (
                dict(self.oracle_demo)
                if isinstance(self.oracle_demo, dict)
                else None
            ),
            "epicDemo": (
                dict(self.epic_demo)
                if isinstance(self.epic_demo, dict)
                else None
            ),
        }


class ScenarioLoader:
    def __init__(
        self,
    ) -> None:
        self._cache: dict[
            tuple[str, int],
            LoadedScenario,
        ] = {}

    def dataset_root(
        self,
    ) -> Path:
        configured = Path(
            settings
            .EVALUATION_INJECTION_DATASET_ROOT
        )

        if configured.is_absolute():
            return configured

        return (
            Path(__file__)
            .resolve()
            .parents[2]
            / configured
        ).resolve()

    def load(
        self,
        scenario_id: str,
        target_rate: int,
    ) -> LoadedScenario:
        cache_key = (
            scenario_id,
            target_rate,
        )

        cached = self._cache.get(
            cache_key
        )

        if cached is not None:
            return cached

        path = (
            self.dataset_root()
            / "episodes"
            / f"{scenario_id}.json"
        )

        if not path.exists():
            raise FileNotFoundError(
                f"Evaluation scenario not found: "
                f"{path}"
            )

        record = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

        if (
            record.get("episodeId")
            != scenario_id
        ):
            raise ValueError(
                "Scenario file episodeId "
                "does not match the requested ID."
            )

        ecg = record.get(
            "ecg"
        ) or {}

        source_rate = int(
            ecg.get(
                "sampleRate"
            )
            or 250
        )

        source_waveforms = (
            ecg.get("waveform")
            or {}
        )

        mapped: dict[
            str,
            np.ndarray,
        ] = {}

        for source_name, lead_id in (
            LEAD_NAME_MAP.items()
        ):
            if lead_id in mapped:
                continue

            values = source_waveforms.get(
                source_name
            )

            if not isinstance(
                values,
                list,
            ):
                continue

            array = np.asarray(
                values,
                dtype=np.float64,
            )

            array = array[
                np.isfinite(array)
            ]

            if array.size:
                mapped[lead_id] = (
                    self.resample(
                        array,
                        source_rate,
                        target_rate,
                    )
                )

        missing = [
            lead_id
            for lead_id
            in DISPLAY_LEADS
            if lead_id not in mapped
        ]

        if missing:
            raise ValueError(
                "Scenario is missing required "
                f"lead(s): {missing}"
            )

        target_length = min(
            len(mapped[lead_id])
            for lead_id
            in DISPLAY_LEADS
        )

        raw_mapped = {
            lead_id: (
                np.asarray(
                    mapped[lead_id][
                        :target_length
                    ],
                    dtype=np.float32,
                )
            )
            for lead_id
            in DISPLAY_LEADS
        }

        baseline_offsets_mv: dict[
            str,
            float,
        ] = {}

        mapped = {}

        for lead_id, values in (
            raw_mapped.items()
        ):
            baseline = float(
                np.median(values)
            )

            baseline_offsets_mv[
                lead_id
            ] = round(
                baseline,
                6,
            )

            mapped[lead_id] = (
                values
                - baseline
            ).astype(
                np.float32
            )

        normalized = {}

        for lead_id, values in (
            mapped.items()
        ):
            scale = float(
                np.quantile(
                    np.abs(values),
                    0.995,
                )
                or 1.0
            )

            normalized[lead_id] = (
                np.clip(
                    values / scale,
                    -1.0,
                    1.0,
                ).astype(
                    np.float32
                )
            )

        measurements = (
            ecg.get(
                "measurements"
            )
            or {}
        )

        episode = (
            record.get("episode")
            or {}
        )

        trigger_heart_rate = (
            finite_number(
                measurements.get(
                    "ventricularRateBpm"
                )
                or measurements.get(
                    "heartRateBpm"
                )
                or episode.get(
                    "triggerHeartRate"
                )
            )
        )

        qrs_duration_ms = (
            finite_number(
                measurements.get(
                    "qrsDurationMs"
                )
            )
        )

        result = LoadedScenario(
            scenario_id=scenario_id,
            display=str(
                episode.get("display")
                or scenario_id
            ),
            severity=str(
                episode.get("severity")
                or "warning"
            ),
            patient=dict(
                record.get("patient")
                or {}
            ),
            episode=dict(episode),
            vitals=dict(
                record.get("vitals")
                or {}
            ),
            labs=dict(
                record.get("labs")
                or {}
            ),
            medications=(
                record.get(
                    "medications"
                )
                or (
                    record.get(
                        "patient"
                    )
                    or {}
                ).get(
                    "homeMedications"
                )
                or []
            ),
            clinical_context=dict(
                record.get(
                    "clinicalContext"
                )
                or {}
            ),
            ecg_measurements=dict(
                measurements
            ),
            sample_rate=target_rate,
            duration_seconds=(
                target_length
                / max(
                    target_rate,
                    1,
                )
            ),
            waveforms_mv=mapped,
            normalized_waveforms=(
                normalized
            ),
            baseline_offsets_mv=(
                baseline_offsets_mv
            ),
            trigger_heart_rate=(
                int(
                    round(
                        trigger_heart_rate
                    )
                )
                if trigger_heart_rate
                is not None
                else None
            ),
            qrs_duration_ms=(
                qrs_duration_ms
            ),
        )

        self._cache[
            cache_key
        ] = result

        return result

    @staticmethod
    def resample(
        values: np.ndarray,
        source_rate: int,
        target_rate: int,
    ) -> np.ndarray:
        if source_rate == target_rate:
            return values.copy()

        duration = (
            len(values)
            / max(
                source_rate,
                1,
            )
        )

        target_count = max(
            1,
            int(
                round(
                    duration
                    * target_rate
                )
            ),
        )

        source_x = np.linspace(
            0.0,
            duration,
            num=len(values),
            endpoint=False,
        )

        target_x = np.linspace(
            0.0,
            duration,
            num=target_count,
            endpoint=False,
        )

        return np.interp(
            target_x,
            source_x,
            values,
        )


class EvaluationInjectionService:
    def __init__(
        self,
    ) -> None:
        self._lock = RLock()
        self._sessions: dict[
            str,
            InjectionSession,
        ] = {}
        self._loader = ScenarioLoader()

    @property
    def enabled(
        self,
    ) -> bool:
        return bool(
            settings
            .EVALUATION_INJECTION_ENABLED
        )

    def arm(
        self,
        *,
        session_id: str,
        scenario_id: str,
        baseline_seconds: float,
        pre_seconds: float,
        post_seconds: float,
        run_slm: bool,
        oracle_demo: dict[str, Any] | None = None,
        epic_demo: dict[str, Any] | None = None,
        token_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise PermissionError(
                "Evaluation injection is disabled."
            )

        if (
            scenario_id
            not in settings
            .EVALUATION_INJECTION_ALLOWED_SCENARIOS
        ):
            raise ValueError(
                "Allowed evaluation scenarios: "
                + ", ".join(
                    settings
                    .EVALUATION_INJECTION_ALLOWED_SCENARIOS
                )
            )

        target_rate = int(
            settings
            .WAVEFORM_SAMPLE_RATE
        )

        scenario = self._loader.load(
            scenario_id,
            target_rate,
        )

        session = InjectionSession(
            session_id=session_id,
            scenario=scenario,
            baseline_seconds=(
                baseline_seconds
            ),
            pre_seconds=(
                pre_seconds
            ),
            post_seconds=(
                post_seconds
            ),
            run_slm=run_slm,
            oracle_demo=(
                dict(oracle_demo)
                if isinstance(oracle_demo, dict)
                else None
            ),
            epic_demo=(
                dict(epic_demo)
                if isinstance(epic_demo, dict)
                else None
            ),
            # SMART EHR context is authentication and routing only. The
            # evaluation session never retains the token for clinical context.
            token_override=None,
        )

        with self._lock:
            previous = self._sessions.get(
                session_id
            )

            if (
                previous is not None
                and previous.state
                not in {
                    "COMPLETE",
                    "FAILED",
                    "CANCELLED",
                }
            ):
                raise RuntimeError(
                    "An evaluation injection is "
                    "already active for this stream."
                )

            self._sessions[
                session_id
            ] = session

        self.publish(
            {
                "type": (
                    "evaluation.injection.armed"
                ),
                **session.public_status(),
            }
        )

        print(
            "[KGEN EVAL INJECTION ARMED]",
            session.public_status(),
        )

        return session.public_status()

    def cancel(
        self,
        session_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(
                session_id
            )

            if session is None:
                raise FileNotFoundError(
                    session_id
                )

            if session.final_task_started:
                raise RuntimeError(
                    "Analysis has already started and cannot be cancelled."
                )

            session.state = "CANCELLED"
            session.updated_at = now_iso()

        status = session.public_status()

        self.publish(
            {
                "type": (
                    "evaluation.injection.cancelled"
                ),
                **status,
            }
        )

        return status

    def status(
        self,
        session_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(
                session_id
            )

            if session is None:
                return {
                    "enabled": self.enabled,
                    "sessionId": session_id,
                    "state": "IDLE",
                }

            return session.public_status()

    def process_frame(
        self,
        *,
        session_id: str,
        frame: dict[str, Any],
    ) -> dict[str, Any]:
        frame_source = (
            normalize_waveform_source(
                frame.get("source")
            )
        )

        if (
            not self.enabled
            or frame_source
            not in SUPPORTED_EVALUATION_BASE_SOURCES
        ):
            return frame

        with self._lock:
            session = self._sessions.get(
                session_id
            )

            if session is None:
                return frame

            if session.state in {
                "COMPLETE",
                "FAILED",
                "CANCELLED",
            }:
                return frame

            # Oracle/Epic automatic evaluation is explicitly API Range +
            # synthetic episode + API Range. A stale legacy INCART SSE request
            # must not be allowed to claim or change the base source for the
            # same waveform session. Ignore it rather than failing the capture.
            if (
                (session.oracle_demo or session.epic_demo)
                and not is_api_range_source(frame_source)
            ):
                print(
                    "[KGEN EVAL IGNORE NON-API-RANGE SOURCE]",
                    {
                        "sessionId": session_id,
                        "scenarioId": session.scenario.scenario_id,
                        "ignoredSource": frame_source,
                        "requiredSource": "api-range",
                        "state": session.state,
                    },
                    flush=True,
                )

                output = dict(frame)
                output["evaluationInjection"] = {
                    **session.public_status(),
                    "suppressNormalEpisodeObserver": True,
                }
                return output

            if session.state == "ANALYZING":
                output = dict(frame)

                output[
                    "evaluationInjection"
                ] = {
                    **session.public_status(),
                    "suppressNormalEpisodeObserver": True,
                }

                return output

            return self._process_active_frame(
                session,
                frame,
            )

    def _initialize_buffers(
        self,
        session: InjectionSession,
        sample_rate: int,
    ) -> None:
        if session.sample_rate is not None:
            return

        session.sample_rate = sample_rate

        pre_samples = max(
            1,
            int(
                round(
                    sample_rate
                    * session.pre_seconds
                )
            ),
        )

        session.pre_buffers = {
            lead_id: deque(
                maxlen=pre_samples
            )
            for lead_id
            in DISPLAY_LEADS
        }

        session.capture_buffers = {
            lead_id: []
            for lead_id
            in DISPLAY_LEADS
        }

        session.detector_buffers = {
            lead_id: []
            for lead_id
            in (
                "lead1",
                "lead2",
                "avf",
            )
        }

    def _fail_source_capture(
        self,
        session: InjectionSession,
        message: str,
    ) -> None:
        if session.state == "FAILED":
            return

        session.state = "FAILED"
        session.error = message
        session.updated_at = now_iso()

        payload = {
            "type": (
                "evaluation.injection.failed"
            ),
            **session.public_status(),
        }

        self.publish(payload)

        print(
            "[KGEN EVAL SOURCE FAILURE]",
            payload,
            flush=True,
        )

    def _track_base_waveform_frame(
        self,
        session: InjectionSession,
        frame: dict[str, Any],
        batch_size: int,
    ) -> None:
        source_name = (
            normalize_waveform_source(
                frame.get("source")
            )
        )

        if (
            source_name
            not in SUPPORTED_EVALUATION_BASE_SOURCES
        ):
            self._fail_source_capture(
                session,
                "Unsupported evaluation base waveform source: "
                f"{source_name or 'unknown'}.",
            )
            return

        if session.base_waveform_source is None:
            session.base_waveform_source = (
                source_name
            )
            session.base_waveform_record = (
                str(
                    frame.get("record")
                    or ""
                ).strip()
                or None
            )
            session.base_source_sample_rate = (
                finite_number(
                    frame.get(
                        "sourceSampleRate"
                    )
                )
            )
            session.base_buffer_samples = (
                int(
                    finite_number(
                        frame.get(
                            "bufferSamples"
                        ),
                        0,
                    )
                    or 0
                )
                or None
            )
            session.base_buffer_seconds = (
                finite_number(
                    frame.get(
                        "bufferSeconds"
                    )
                )
            )
            session.base_cursor_start = int(
                finite_number(
                    frame.get("cursor"),
                    0,
                )
                or 0
            )

        elif (
            source_name
            != session.base_waveform_source
        ):
            self._fail_source_capture(
                session,
                "The evaluation waveform source changed from "
                f"{session.base_waveform_source!r} "
                f"to {source_name!r}.",
            )
            return

        cursor = int(
            finite_number(
                frame.get("cursor"),
                0,
            )
            or 0
        )
        next_cursor = int(
            finite_number(
                frame.get("nextCursor"),
                cursor + batch_size,
            )
            or (
                cursor
                + batch_size
            )
        )

        session.base_cursor_end = (
            next_cursor
        )
        session.base_source_frames_seen += 1

        if not is_api_range_source(
            source_name
        ):
            return

        buffer_samples = int(
            finite_number(
                frame.get(
                    "bufferSamples"
                ),
                0,
            )
            or 0
        )

        if buffer_samples <= 0:
            self._fail_source_capture(
                session,
                "API Range did not report a usable waveform buffer.",
            )
            return

        if next_cursor < cursor:
            session.base_source_wrapped = (
                True
            )
            session.base_source_wrap_count += (
                1
            )
            session.base_source_replayed = (
                True
            )

            if (
                session.api_range_capture_mode
                == "continuous"
            ):
                self._fail_source_capture(
                    session,
                    "API Range wrapped to the beginning during evaluation. "
                    "Choose a longer timestamp range or set "
                    "EVALUATION_API_RANGE_CAPTURE_MODE=repeat_snapshot.",
                )
                return

        if (
            session.base_source_frames_seen
            != 1
        ):
            return

        event_samples = min(
            len(
                session.scenario
                .waveforms_mv[
                    lead_id
                ]
            )
            for lead_id
            in DISPLAY_LEADS
        )

        active_rate = max(
            int(
                session.sample_rate
                or frame.get(
                    "sampleRate"
                )
                or settings
                .WAVEFORM_SAMPLE_RATE
            ),
            1,
        )

        required_samples = (
            int(
                round(
                    active_rate
                    * session.baseline_seconds
                )
            )
            + event_samples
            + int(
                round(
                    active_rate
                    * session.post_seconds
                )
            )
            + int(
                round(
                    active_rate
                    * API_RANGE_CAPTURE_HEADROOM_SECONDS
                )
            )
        )

        available_without_wrap = (
            buffer_samples
            - cursor
        )

        if (
            available_without_wrap
            < required_samples
        ):
            session.base_source_replayed = (
                True
            )

            if (
                session.api_range_capture_mode
                == "continuous"
            ):
                self._fail_source_capture(
                    session,
                    "API Range is too short for a continuous evaluation "
                    "without replaying earlier samples. "
                    f"requiredSamples={required_samples}; "
                    f"availableSamples={available_without_wrap}; "
                    f"bufferSamples={buffer_samples}; "
                    f"startCursor={cursor}.",
                )
                return

            print(
                "[KGEN API RANGE SNAPSHOT REPLAY]",
                {
                    "sessionId": (
                        session.session_id
                    ),
                    "scenarioId": (
                        session.scenario
                        .scenario_id
                    ),
                    "bufferSamples": (
                        buffer_samples
                    ),
                    "availableSamples": (
                        available_without_wrap
                    ),
                    "requiredSamples": (
                        required_samples
                    ),
                    "captureMode": (
                        session
                        .api_range_capture_mode
                    ),
                },
                flush=True,
            )

    def _prepare_injection_waveforms(
        self,
        session: InjectionSession,
    ) -> None:
        if session.injection_waveforms_mv:
            return

        sample_rate = max(
            int(
                session.sample_rate
                or settings
                .WAVEFORM_SAMPLE_RATE
            ),
            1,
        )
        calibration_window = max(
            1,
            int(
                round(
                    sample_rate * 4.0
                )
            ),
        )

        for lead_id in DISPLAY_LEADS:
            pre_values = np.asarray(
                list(
                    session.pre_buffers[
                        lead_id
                    ]
                )[-calibration_window:],
                dtype=np.float32,
            )
            base_center, base_scale = (
                robust_center_scale(
                    pre_values
                )
            )

            raw_scenario = np.asarray(
                session.scenario
                .waveforms_mv[lead_id],
                dtype=np.float32,
            )
            raw_center, raw_scale = (
                robust_center_scale(
                    raw_scenario
                )
            )
            raw_centered = (
                raw_scenario
                - raw_center
            )

            if raw_scale > 0:
                morphology = (
                    raw_centered
                    / raw_scale
                )
            else:
                morphology = np.asarray(
                    session.scenario
                    .normalized_waveforms[
                        lead_id
                    ],
                    dtype=np.float32,
                )

            morphology = np.nan_to_num(
                morphology,
                nan=0.0,
                posinf=0.0,
                neginf=0.0,
            ).astype(np.float32)

            # The scenario JSON waveform may be normalized rather than mV.
            # Scale its morphology to the surrounding API/INCART physical
            # amplitude so the persisted event cannot collapse into a flat line.
            target_scale = float(
                np.clip(
                    base_scale,
                    INJECTION_MIN_SIGNAL_SCALE_MV,
                    INJECTION_MAX_SIGNAL_SCALE_MV,
                )
            )

            calibrated = (
                morphology
                * target_scale
                + base_center
            ).astype(np.float32)

            entry_value = (
                float(pre_values[-1])
                if pre_values.size
                else base_center
            )
            entry_slope = (
                float(pre_values[-1] - pre_values[-2])
                if pre_values.size >= 2
                else 0.0
            )

            session.injection_waveforms_mv[
                lead_id
            ] = calibrated
            session.injection_display_centers_mv[
                lead_id
            ] = base_center
            session.injection_display_scales_mv[
                lead_id
            ] = target_scale
            session.injection_entry_value_mv[
                lead_id
            ] = entry_value
            session.injection_entry_slope_mv[
                lead_id
            ] = entry_slope
            session.injection_calibration[
                lead_id
            ] = {
                "baseCenterMv": round(base_center, 6),
                "baseScaleMv": round(base_scale, 6),
                "scenarioSourceCenter": round(raw_center, 6),
                "scenarioSourceScale": round(raw_scale, 6),
                "targetScaleMv": round(target_scale, 6),
                "entryAnchorMv": round(entry_value, 6),
                "entrySlopeMvPerSample": round(entry_slope, 6),
            }

        print(
            "[KGEN EVAL WAVEFORM CALIBRATION]",
            {
                "sessionId": (
                    session.session_id
                ),
                "scenarioId": (
                    session.scenario
                    .scenario_id
                ),
                "method": (
                    "endpoint-constrained-hermite-v2"
                ),
                "transitionSeconds": (
                    INJECTION_TRANSITION_SECONDS
                ),
                "leads": (
                    session
                    .injection_calibration
                ),
            },
            flush=True,
        )

    def _display_batch_from_mv(
        self,
        session: InjectionSession,
        batch: dict[
            str,
            np.ndarray,
        ],
    ) -> dict[str, list[float]]:
        display: dict[
            str,
            list[float],
        ] = {}

        for lead_id in DISPLAY_LEADS:
            center = float(
                session
                .injection_display_centers_mv
                .get(
                    lead_id,
                    0.0,
                )
            )
            scale = float(
                session
                .injection_display_scales_mv
                .get(
                    lead_id,
                    INJECTION_MIN_SIGNAL_SCALE_MV,
                )
            )
            scale = max(
                scale,
                INJECTION_MIN_SIGNAL_SCALE_MV,
            )

            normalized = np.clip(
                (
                    np.asarray(
                        batch[lead_id],
                        dtype=np.float32,
                    )
                    - center
                )
                / scale,
                -1.0,
                1.0,
            )

            display[lead_id] = [
                round(
                    float(value),
                    5,
                )
                for value in normalized
            ]

        return display

    def _process_active_frame(
        self,
        session: InjectionSession,
        frame: dict[str, Any],
    ) -> dict[str, Any]:
        sample_rate = int(
            frame.get("sampleRate")
            or settings
            .WAVEFORM_SAMPLE_RATE
        )

        self._initialize_buffers(
            session,
            sample_rate,
        )

        batch = self._frame_arrays(
            frame
        )

        batch_size = min(
            len(batch[lead_id])
            for lead_id
            in DISPLAY_LEADS
        )

        if batch_size <= 0:
            return frame

        self._track_base_waveform_frame(
            session,
            frame,
            batch_size,
        )

        if session.state == "FAILED":
            output = dict(frame)
            output[
                "evaluationInjection"
            ] = {
                **session.public_status(),
                "suppressNormalEpisodeObserver": True,
            }
            return output

        batch = {
            lead_id: values[
                :batch_size
            ]
            for lead_id, values
            in batch.items()
        }

        baseline_target = int(
            round(
                sample_rate
                * session.baseline_seconds
            )
        )

        if (
            session.state == "ARMED"
            and session.baseline_samples_seen
            >= baseline_target
        ):
            self._start_injection(
                session
            )

        output_batch = batch

        if session.state == "ARMED":
            self._append_pre(
                session,
                batch,
            )

            session.baseline_samples_seen += (
                batch_size
            )

        elif session.state == "INJECTING":
            scenario_length = min(
                len(
                    session
                    .scenario
                    .waveforms_mv[
                        lead_id
                    ]
                )
                for lead_id
                in DISPLAY_LEADS
            )

            remaining_event_samples = max(
                0,
                scenario_length
                - session.scenario_cursor,
            )
            event_samples_in_batch = min(
                batch_size,
                remaining_event_samples,
            )

            output_batch = (
                self._inject_batch(
                    session,
                    batch,
                )
            )

            self._append_capture(
                session,
                output_batch,
            )

            self._update_detector(
                session,
                output_batch,
            )

            if (
                session.scenario_cursor
                >= scenario_length
            ):
                session.reference_end_abs = (
                    session.global_samples_seen
                    + event_samples_in_batch
                )

                session.post_samples_seen += max(
                    0,
                    batch_size
                    - event_samples_in_batch,
                )

                session.state = (
                    "POST_EVENT"
                )

                self.publish(
                    {
                        "type": (
                            "evaluation.injection.event_complete"
                        ),
                        **session.public_status(),
                    }
                )

        elif session.state == "POST_EVENT":
            output_batch = self._apply_post_transition(
                session,
                batch,
            )

            self._append_capture(
                session,
                output_batch,
            )

            session.post_samples_seen += (
                batch_size
            )

            post_target = int(
                round(
                    sample_rate
                    * session.post_seconds
                )
            )

            if (
                session.post_samples_seen
                >= post_target
                and not session
                .final_task_started
            ):
                session.final_task_started = (
                    True
                )

                if (
                    session.detected_trigger_abs
                    is None
                ):
                    session.state = "FAILED"
                    session.error = (
                        "The configured evaluation trigger policy did not create a capture trigger."
                    )

                    self.publish(
                        {
                            "type": (
                                "evaluation.injection.failed"
                            ),
                            **session
                            .public_status(),
                        }
                    )

                else:
                    session.state = (
                        "ANALYZING"
                    )

                    try:
                        loop = (
                            asyncio
                            .get_running_loop()
                        )

                        loop.create_task(
                            self._finalize(
                                session.session_id
                            )
                        )

                    except RuntimeError:
                        session.state = (
                            "FAILED"
                        )
                        session.error = (
                            "No running event loop "
                            "was available for "
                            "analysis."
                        )

        session.global_samples_seen += (
            batch_size
        )
        session.updated_at = now_iso()

        output = dict(frame)

        if output_batch is not batch:
            output = self._apply_batch(
                output,
                output_batch,
                session,
            )

        status = session.public_status()

        output[
            "evaluationInjection"
        ] = {
            **status,
            "suppressNormalEpisodeObserver": (
                session.state
                not in {
                    "COMPLETE",
                    "FAILED",
                    "CANCELLED",
                }
            ),
        }

        return output

    def _start_injection(
        self,
        session: InjectionSession,
    ) -> None:
        session.state = "INJECTING"
        session.reference_onset_abs = (
            session.global_samples_seen
        )
        session.scenario_cursor = 0

        self._prepare_injection_waveforms(
            session
        )

        for lead_id in DISPLAY_LEADS:
            session.capture_buffers[
                lead_id
            ] = list(
                session.pre_buffers[
                    lead_id
                ]
            )

        self.publish(
            {
                "type": (
                    "evaluation.injection.started"
                ),
                **session.public_status(),
            }
        )

        print(
            "[KGEN EVAL INJECTION START]",
            session.public_status(),
        )

    def _frame_arrays(
        self,
        frame: dict[str, Any],
    ) -> dict[
        str,
        np.ndarray,
    ]:
        source = (
            frame.get("leadsMv")
            or {}
        )

        return {
            lead_id: np.asarray(
                source.get(lead_id)
                or [],
                dtype=np.float32,
            )
            for lead_id
            in DISPLAY_LEADS
        }

    def _append_pre(
        self,
        session: InjectionSession,
        batch: dict[
            str,
            np.ndarray,
        ],
    ) -> None:
        for lead_id in DISPLAY_LEADS:
            session.pre_buffers[
                lead_id
            ].extend(
                float(value)
                for value
                in batch[lead_id]
            )

    def _append_capture(
        self,
        session: InjectionSession,
        batch: dict[
            str,
            np.ndarray,
        ],
    ) -> None:
        for lead_id in DISPLAY_LEADS:
            session.capture_buffers[
                lead_id
            ].extend(
                float(value)
                for value
                in batch[lead_id]
            )

    def _apply_post_transition(
        self,
        session: InjectionSession,
        batch: dict[str, np.ndarray],
        start_offset: int = 0,
    ) -> dict[str, np.ndarray]:
        """Bridge the episode endpoint into the advancing source over 100 ms."""
        batch_size = min(len(batch[lead_id]) for lead_id in DISPLAY_LEADS)
        start_offset = max(0, min(int(start_offset), batch_size))
        if start_offset >= batch_size:
            return batch

        transition_samples = transition_sample_count(
            session.sample_rate or settings.WAVEFORM_SAMPLE_RATE
        )
        if session.post_transition_samples_seen >= transition_samples:
            return batch

        output = {
            lead_id: batch[lead_id].copy()
            for lead_id in DISPLAY_LEADS
        }
        available = batch_size - start_offset

        for lead_id in DISPLAY_LEADS:
            api_values = np.asarray(
                batch[lead_id][start_offset:batch_size],
                dtype=np.float32,
            )
            if not api_values.size:
                continue

            if lead_id not in session.post_api_start_value_mv:
                session.post_api_start_value_mv[lead_id] = float(api_values[0])
                session.post_api_start_slope_mv[lead_id] = (
                    float(api_values[1] - api_values[0])
                    if api_values.size >= 2
                    else 0.0
                )

            api_start_value = session.post_api_start_value_mv[lead_id]
            api_start_slope = session.post_api_start_slope_mv[lead_id]
            event_end_value = session.injection_last_value_mv.get(
                lead_id,
                api_start_value,
            )
            event_end_slope = session.injection_last_slope_mv.get(
                lead_id,
                0.0,
            )
            value_delta = event_end_value - api_start_value
            slope_delta = event_end_slope - api_start_slope

            for local_offset in range(available):
                absolute_post_index = (
                    session.post_transition_samples_seen + local_offset
                )
                if absolute_post_index >= transition_samples:
                    break
                progress = absolute_post_index / transition_samples
                correction = cubic_decay_correction(
                    progress,
                    value_delta,
                    slope_delta,
                    transition_samples,
                )
                output[lead_id][start_offset + local_offset] = (
                    float(api_values[local_offset]) + correction
                )

        session.post_transition_samples_seen += available
        return output

    def _inject_batch(
        self,
        session: InjectionSession,
        original: dict[str, np.ndarray],
    ) -> dict[str, np.ndarray]:
        batch_size = len(original["lead2"])
        scenario_length = min(
            len(
                session.injection_waveforms_mv.get(
                    lead_id,
                    session.scenario.waveforms_mv[lead_id],
                )
            )
            for lead_id in DISPLAY_LEADS
        )
        remaining = max(0, scenario_length - session.scenario_cursor)
        take = min(batch_size, remaining)
        output: dict[str, np.ndarray] = {}
        transition_samples = transition_sample_count(
            session.sample_rate or settings.WAVEFORM_SAMPLE_RATE
        )

        for lead_id in DISPLAY_LEADS:
            values = original[lead_id].copy()
            waveform = np.asarray(
                session.injection_waveforms_mv.get(
                    lead_id,
                    session.scenario.waveforms_mv[lead_id],
                ),
                dtype=np.float32,
            )

            if take:
                injected = waveform[
                    session.scenario_cursor:
                    session.scenario_cursor + take
                ]
                scenario_start_value = float(waveform[0])
                scenario_start_slope = (
                    float(waveform[1] - waveform[0])
                    if waveform.size >= 2
                    else 0.0
                )
                entry_value = session.injection_entry_value_mv.get(
                    lead_id,
                    scenario_start_value,
                )
                entry_slope = session.injection_entry_slope_mv.get(
                    lead_id,
                    scenario_start_slope,
                )
                value_delta = entry_value - scenario_start_value
                slope_delta = entry_slope - scenario_start_slope

                for offset in range(take):
                    absolute_event_index = session.scenario_cursor + offset
                    value = float(injected[offset])
                    if absolute_event_index < transition_samples:
                        progress = absolute_event_index / transition_samples
                        value += cubic_decay_correction(
                            progress,
                            value_delta,
                            slope_delta,
                            transition_samples,
                        )
                    values[offset] = value

                event_values = values[:take]
                previous_value = session.injection_last_value_mv.get(lead_id)
                if event_values.size >= 2:
                    final_slope = float(event_values[-1] - event_values[-2])
                elif previous_value is not None:
                    final_slope = float(event_values[-1] - previous_value)
                else:
                    final_slope = 0.0
                session.injection_last_value_mv[lead_id] = float(event_values[-1])
                session.injection_last_slope_mv[lead_id] = final_slope

            output[lead_id] = values

        session.scenario_cursor += take

        if session.scenario_cursor >= scenario_length and take < batch_size:
            output = self._apply_post_transition(
                session,
                output,
                start_offset=take,
            )

        return output

    def _apply_batch(
        self,
        frame: dict[str, Any],
        batch: dict[
            str,
            np.ndarray,
        ],
        session: InjectionSession,
    ) -> dict[str, Any]:
        # Persisted physical mV and live normalized display now come from the
        # same blended batch. This removes the previous mismatch where the
        # live page showed scenario-normalized data but Analytics stored a
        # near-zero physical event.
        frame["leadsMv"] = {
            lead_id: [
                round(
                    float(value),
                    5,
                )
                for value
                in batch[lead_id]
            ]
            for lead_id
            in DISPLAY_LEADS
        }

        frame["leads"] = (
            self._display_batch_from_mv(
                session,
                batch,
            )
        )

        frame["latestMv"] = {
            lead_id: round(
                float(
                    batch[lead_id][-1]
                ),
                3,
            )
            for lead_id
            in DISPLAY_LEADS
        }

        frame["source"] = (
            "evaluation-injection"
        )
        frame["evaluationScenario"] = {
            "scenarioId": (
                session.scenario
                .scenario_id
            ),
            "display": (
                session.scenario
                .display
            ),
            "severity": (
                session.scenario
                .severity
            ),
            "blendMethod": (
                "endpoint-constrained-hermite-v2"
            ),
            "transitionSeconds": (
                INJECTION_TRANSITION_SECONDS
            ),
            "addsRuntimeDelay": False,
        }

        return frame

    def _update_detector(
        self,
        session: InjectionSession,
        batch: dict[str, np.ndarray],
    ) -> None:
        if session.detected_trigger_abs is not None:
            return

        rate = max(
            session.sample_rate or settings.WAVEFORM_SAMPLE_RATE,
            1,
        )
        policy = detector_policy(session.scenario.scenario_id)
        monitored_leads = ("lead1", "lead2", "avf")

        for lead_id in monitored_leads:
            session.detector_buffers[lead_id].extend(
                float(value) for value in batch[lead_id]
            )

        hold_seconds = float(policy.get("holdSeconds") or 1.2)
        required_samples = max(1, int(round(rate * hold_seconds)))

        if len(session.detector_buffers["lead2"]) < required_samples:
            return

        if policy.get("mode") == "existing_vt_detector":
            window_samples = int(round(rate * 1.6))
            estimates = []

            for lead_id in monitored_leads:
                values = np.asarray(
                    session.detector_buffers[lead_id][-window_samples:],
                    dtype=np.float64,
                )
                estimate = self._periodic_rate(values, rate)
                if estimate is not None:
                    estimates.append(estimate)

            if len(estimates) < 2:
                return

            rates = [item[0] for item in estimates]
            scores = [item[1] for item in estimates]
            estimated_rate = float(np.median(rates))
            regularity_score = float(np.median(scores))
            lead_rate_spread = max(rates) - min(rates)

            if (
                estimated_rate < settings.EVALUATION_INJECTION_VT_RATE_THRESHOLD
                or regularity_score < 0.25
                or lead_rate_spread > 25.0
            ):
                return

            session.detected_trigger_abs = (
                session.reference_onset_abs or session.global_samples_seen
            ) + required_samples
            session.detector_rule_id = "sustained-multilead-tachycardia-v2"
            session.detector_rate_bpm = estimated_rate

        else:
            changed_leads = 0

            for lead_id in monitored_leads:
                baseline = np.asarray(
                    list(session.pre_buffers.get(lead_id) or [])[-required_samples:],
                    dtype=np.float64,
                )
                event = np.asarray(
                    session.detector_buffers[lead_id][-required_samples:],
                    dtype=np.float64,
                )

                if baseline.size < max(8, required_samples // 2) or event.size < 8:
                    continue

                baseline_centered = baseline - np.median(baseline)
                event_centered = event - np.median(event)
                baseline_energy = float(np.quantile(np.abs(baseline_centered), 0.90))
                event_energy = float(np.quantile(np.abs(event_centered), 0.90))
                baseline_slope = float(np.median(np.abs(np.diff(baseline))))
                event_slope = float(np.median(np.abs(np.diff(event))))

                if (
                    event_energy > max(0.04, baseline_energy * 1.35)
                    or event_slope > max(0.015, baseline_slope * 1.35)
                ):
                    changed_leads += 1

            fallback_seconds = float(policy.get("fallbackSeconds") or 1.5)
            fallback_samples = max(required_samples, int(round(rate * fallback_seconds)))
            fallback_reached = session.scenario_cursor >= fallback_samples

            if changed_leads < 2 and not fallback_reached:
                return

            latency_samples = required_samples if changed_leads >= 2 else fallback_samples
            session.detected_trigger_abs = (
                session.reference_onset_abs or session.global_samples_seen
            ) + latency_samples
            session.detector_rule_id = (
                "multilead-waveform-change-v1"
                if changed_leads >= 2
                else "controlled-capture-fallback-v1"
            )
            session.detector_rate_bpm = session.scenario.trigger_heart_rate

        self.publish(
            {
                "type": "evaluation.injection.detected",
                **session.public_status(),
            }
        )
        print("[KGEN EVAL INJECTION DETECTED]", session.public_status())

    @staticmethod
    def _periodic_rate(
        values: np.ndarray,
        sample_rate: int,
    ) -> tuple[
        float,
        float,
    ] | None:
        if values.size < sample_rate:
            return None

        centered = (
            values
            - np.median(values)
        )

        p2p = float(
            np.quantile(
                centered,
                0.98,
            )
            - np.quantile(
                centered,
                0.02,
            )
        )

        if p2p < 0.2:
            return None

        centered = centered - np.mean(
            centered
        )

        energy = float(
            np.dot(
                centered,
                centered,
            )
        )

        if energy <= 1e-9:
            return None

        autocorrelation = np.correlate(
            centered,
            centered,
            mode="full",
        )[
            len(centered) - 1:
        ]

        minimum_lag = max(
            1,
            int(
                sample_rate
                * 60
                / 240
            ),
        )

        maximum_lag = min(
            len(autocorrelation)
            - 1,
            int(
                sample_rate
                * 60
                / 120
            ),
        )

        if maximum_lag <= minimum_lag:
            return None

        section = autocorrelation[
            minimum_lag:
            maximum_lag + 1
        ]

        best_relative = int(
            np.argmax(section)
        )

        best_lag = (
            minimum_lag
            + best_relative
        )

        score = float(
            section[best_relative]
            / max(
                autocorrelation[0],
                1e-9,
            )
        )

        rate = (
            60.0
            * sample_rate
            / best_lag
        )

        return rate, score

    async def _finalize(
        self,
        session_id: str,
    ) -> None:
        finalization_stage = "load_session"

        try:
            with self._lock:
                session = self._sessions[
                    session_id
                ]

            print(
                "[KGEN EVAL INJECTION ANALYSIS START]",
                session.public_status(),
            )

            # Persisting the capture is the only truly fatal finalization step.
            # Once the 20-second capture exists, downstream enrichment failures
            # must not turn a valid captured episode into "Evaluation Failed".
            finalization_stage = "persist_episode"
            metadata = await asyncio.to_thread(
                self._persist_episode,
                session,
            )

            session.episode_id = (
                metadata["id"]
            )

            session.incident_id = (
                metadata["incidentId"]
            )

            session.updated_at = now_iso()

            self.publish(
                {
                    "type": (
                        "evaluation.injection.captured"
                    ),
                    "mode": (
                        "evaluation_injection"
                    ),
                    **session.public_status(),
                }
            )

            analysis_warnings: list[dict[str, str]] = []

            finalization_stage = "episode_analysis"
            try:
                episode_analysis = (
                    await asyncio.to_thread(
                        episode_analyzer.analyze,
                        session.episode_id,
                        force=True,
                    )
                )
            except Exception as error:
                episode_analysis = {
                    "status": "unavailable",
                    "error": str(error),
                }
                analysis_warnings.append(
                    {
                        "stage": finalization_stage,
                        "errorType": type(error).__name__,
                        "message": str(error),
                    }
                )
                print(
                    "[KGEN EVAL NONFATAL ANALYSIS ERROR]",
                    {
                        "stage": finalization_stage,
                        "sessionId": session_id,
                        "scenarioId": session.scenario.scenario_id,
                        "errorType": type(error).__name__,
                        "message": str(error),
                    },
                    flush=True,
                )

            finalization_stage = "incident_analysis"
            try:
                incident_analysis = (
                    await asyncio.to_thread(
                        incident_analyzer.analyze,
                        session.incident_id,
                        force=True,
                    )
                )
            except Exception as error:
                incident_analysis = {
                    "status": "unavailable",
                    "error": str(error),
                }
                analysis_warnings.append(
                    {
                        "stage": finalization_stage,
                        "errorType": type(error).__name__,
                        "message": str(error),
                    }
                )
                print(
                    "[KGEN EVAL NONFATAL ANALYSIS ERROR]",
                    {
                        "stage": finalization_stage,
                        "sessionId": session_id,
                        "scenarioId": session.scenario.scenario_id,
                        "errorType": type(error).__name__,
                        "message": str(error),
                    },
                    flush=True,
                )

            episode_dir = (
                Path(settings.EPISODE_STORAGE_PATH)
                / session.episode_id
            )

            # V7 evaluation path: the SLM receives the sanitized SLM_Eval episode
            # directly. No Phase 6/Phase 7 deterministic output is generated or
            # supplied to the model. Oracle/Epic SMART remains routing/auth only.
            finalization_stage = "etiology_v7"
            try:
                etiology_result = await run_etiology_v7(
                    scenario_id=session.scenario.scenario_id,
                    episode_id=session.episode_id,
                    incident_id=session.incident_id,
                    episode_dir=episode_dir,
                    run_slm=session.run_slm,
                )
            except Exception as error:
                # The waveform episode/incident is already valid and persisted.
                # Surface model/response unavailability in Analytics instead of
                # incorrectly labelling the complete capture as a failed run.
                etiology_result = {
                    "status": "unavailable",
                    "source": "etiology_v7",
                    "responseFile": None,
                    "model": None,
                    "validation": {},
                    "diagnosticEvent": {},
                    "score": {
                        "schemaVersion": "etiology-v7-runtime-status-v1",
                        "total": None,
                        "safetyPass": None,
                        "overallPass": None,
                        "validContract": False,
                    },
                }
                analysis_warnings.append(
                    {
                        "stage": finalization_stage,
                        "errorType": type(error).__name__,
                        "message": str(error),
                    }
                )
                print(
                    "[KGEN EVAL NONFATAL ETIOLOGY ERROR]",
                    {
                        "stage": finalization_stage,
                        "sessionId": session_id,
                        "scenarioId": session.scenario.scenario_id,
                        "errorType": type(error).__name__,
                        "message": str(error),
                    },
                    flush=True,
                )

            score = (
                etiology_result.get("score")
                if isinstance(etiology_result, dict)
                else None
            ) or {
                "schemaVersion": "etiology-v7-runtime-status-v1",
                "total": None,
                "safetyPass": None,
                "overallPass": None,
                "validContract": False,
            }
            session.score = score

            finalization_stage = "metadata_update"
            try:
                metadata_path = episode_dir / "metadata.json"
                current_metadata = json.loads(
                    metadata_path.read_text(encoding="utf-8")
                )
                current_metadata["analysisStatus"] = episode_analysis.get(
                    "status",
                    "ready",
                )
                current_metadata["evaluationScore"] = score
                current_metadata["etiologyState"] = etiology_result.get("status")
                current_metadata["evaluationModel"] = etiology_result.get("model")
                current_metadata["analysisWarnings"] = analysis_warnings
                current_metadata["cardinalEvaluation"] = {
                    "status": etiology_result.get("status"),
                    "source": etiology_result.get("source"),
                    "responseFile": etiology_result.get("responseFile"),
                    "model": etiology_result.get("model"),
                    "score": score.get("total") if isinstance(score, dict) else None,
                    "safetyPass": (
                        score.get("safetyPass") if isinstance(score, dict) else None
                    ),
                    "overallPass": (
                        score.get("overallPass") if isinstance(score, dict) else None
                    ),
                    "validation": etiology_result.get("validation") or {},
                    "diagnosticEvent": etiology_result.get("diagnosticEvent") or {},
                    "pipeline": "etiology_v7",
                    "phase6Used": False,
                    "phase7OrchestratorUsed": False,
                    "oracleFhirContextUsed": False,
                    "epicFhirContextUsed": False,
                }
                current_metadata["diagnosticEvent"] = (
                    etiology_result.get("diagnosticEvent") or {}
                )
                atomic_json(metadata_path, current_metadata)
            except Exception as error:
                analysis_warnings.append(
                    {
                        "stage": finalization_stage,
                        "errorType": type(error).__name__,
                        "message": str(error),
                    }
                )
                print(
                    "[KGEN EVAL NONFATAL METADATA ERROR]",
                    {
                        "stage": finalization_stage,
                        "sessionId": session_id,
                        "scenarioId": session.scenario.scenario_id,
                        "errorType": type(error).__name__,
                        "message": str(error),
                    },
                    flush=True,
                )

            session.analysis_result = {
                "episodeStatus": episode_analysis.get("status"),
                "incidentStatus": incident_analysis.get("status"),
                "etiologyState": etiology_result.get("status"),
                "cardinalStatus": etiology_result.get("status"),
                "analysisWarnings": analysis_warnings,
                "phase6Used": False,
                "phase7OrchestratorUsed": False,
            }

            session.state = "COMPLETE"
            session.error = None
            session.updated_at = now_iso()

            event = {
                "type": (
                    "evaluation.injection.complete"
                ),
                "mode": (
                    "evaluation_injection"
                ),
                "status": "ready",
                "title": (
                    "Evaluation analysis completed"
                ),
                "message": self._completion_message(
                    session
                ),
                **session.public_status(),
                "score": score,
                "analysisWarnings": analysis_warnings,
            }

            self.publish(event)

            print(
                "[KGEN EVAL INJECTION COMPLETE]",
                event,
            )

        except Exception as error:
            with self._lock:
                session = self._sessions.get(
                    session_id
                )

                if session is not None:
                    session.state = "FAILED"
                    session.error = (
                        f"{finalization_stage}: {error}"
                    )
                    session.updated_at = (
                        now_iso()
                    )

                    status = (
                        session.public_status()
                    )

                else:
                    status = {
                        "sessionId": session_id,
                    }

            failure_event = {
                "type": (
                    "evaluation.injection.failed"
                ),
                "mode": (
                    "evaluation_injection"
                ),
                "failureStage": finalization_stage,
                "errorType": (
                    type(error).__name__
                ),
                "message": str(error),
                **status,
            }

            self.publish(failure_event)

            print(
                "[KGEN EVAL INJECTION ERROR]",
                {
                    "stage": finalization_stage,
                    "errorType": type(error).__name__,
                    "message": str(error),
                    "sessionId": session_id,
                },
                flush=True,
            )

            import traceback
            traceback.print_exc()

    def _persist_episode(
        self,
        session: InjectionSession,
    ) -> dict[str, Any]:
        rate = int(
            session.sample_rate
            or settings
            .WAVEFORM_SAMPLE_RATE
        )

        pre_samples = int(
            round(
                rate
                * session.pre_seconds
            )
        )

        event_samples = min(
            len(
                session.scenario
                .waveforms_mv[
                    lead_id
                ]
            )
            for lead_id
            in DISPLAY_LEADS
        )

        post_samples = int(
            round(
                rate
                * session.post_seconds
            )
        )

        expected = (
            pre_samples
            + event_samples
            + post_samples
        )

        length = min(
            len(
                session.capture_buffers[
                    lead_id
                ]
            )
            for lead_id
            in DISPLAY_LEADS
        )

        length = min(
            length,
            expected,
        )

        matrix = np.column_stack(
            [
                np.asarray(
                    session
                    .capture_buffers[
                        lead_id
                    ][
                        :length
                    ],
                    dtype=np.float32,
                )
                for lead_id
                in DISPLAY_LEADS
            ]
        )

        timestamp = datetime.now(
            timezone.utc
        ).strftime(
            "%Y%m%d-%H%M%S"
        )

        episode_id = (
            "evalflow-"
            + safe_slug(
                session.scenario
                .scenario_id
            )
            + "-"
            + timestamp
            + "-"
            + secrets.token_hex(3)
        )

        episode_dir = (
            Path(
                settings
                .EPISODE_STORAGE_PATH
            )
            / episode_id
        )

        episode_dir.mkdir(
            parents=True,
            exist_ok=False,
        )

        event_start_offset = min(
            pre_samples,
            length,
        )

        event_end_offset = min(
            pre_samples
            + event_samples,
            length,
        )

        detected_offset = (
            (
                session
                .detected_trigger_abs
                - session
                .reference_onset_abs
                + event_start_offset
            )
            if (
                session
                .detected_trigger_abs
                is not None
                and session
                .reference_onset_abs
                is not None
            )
            else event_start_offset
        )

        detected_offset = max(
            event_start_offset,
            min(
                int(
                    detected_offset
                ),
                max(
                    event_start_offset,
                    event_end_offset,
                ),
            ),
        )

        reference_annotation = {
            "id": "reference-onset",
            "kind": "reference",
            "symbol": "EVAL_REF",
            "label": (
                "Reference scenario onset"
            ),
            "display": (
                "Reference scenario onset"
            ),
            "category": (
                "evaluation_reference"
            ),
            "mode": "event",
            "severity": "info",
            "source": (
                "evaluation_injection"
            ),
            "absoluteSample": (
                event_start_offset
            ),
            "captureOffsetSamples": (
                event_start_offset
            ),
            "captureOffsetSeconds": round(
                event_start_offset
                / rate,
                3,
            ),
        }

        trigger_details = detected_annotation_details(
            session.scenario.scenario_id,
            detector_rule_id=session.detector_rule_id,
        )

        detected_annotation = {
            "id": "detected-trigger",
            "kind": "detected",
            "symbol": trigger_details["symbol"],
            "label": trigger_details["label"],
            "display": trigger_details["display"],
            "category": trigger_details["category"],
            "mode": "event",
            "severity": session.scenario.severity,
            "source": trigger_details["source"],
            "ruleId": session.detector_rule_id,
            "triggerMode": trigger_details["triggerMode"],
            "isIndependentDiagnosis": False,
            "absoluteSample": detected_offset,
            "captureOffsetSamples": detected_offset,
            "captureOffsetSeconds": round(detected_offset / rate, 3),
        }

        trigger_annotations = [
            detected_annotation,
        ]

        all_annotations = [
            reference_annotation,
            detected_annotation,
        ]

        np.savez_compressed(
            episode_dir
            / "waveforms.npz",
            centered_mv=matrix,
            raw_mv=matrix.copy(),
            lead_ids=np.asarray(
                DISPLAY_LEADS,
                dtype="U16",
            ),
            lead_names=np.asarray(
                OUTPUT_LEAD_NAMES,
                dtype="U16",
            ),
            sample_rate=np.asarray(
                rate
            ),
            event_start_offset=np.asarray(
                event_start_offset
            ),
            event_end_offset=np.asarray(
                event_end_offset
            ),
        )

        duration = (
            length / rate
        )

        actual_pre = (
            event_start_offset
            / rate
        )

        actual_post = max(
            0,
            length
            - event_end_offset
        ) / rate

        reference_onset_seconds = (
            event_start_offset
            / rate
        )

        detected_seconds = (
            detected_offset
            / rate
        )

        trigger_latency = (
            detected_seconds
            - reference_onset_seconds
        )

        oracle_demo = (
            dict(session.oracle_demo)
            if isinstance(session.oracle_demo, dict)
            else {}
        )
        epic_demo = (
            dict(session.epic_demo)
            if isinstance(session.epic_demo, dict)
            else {}
        )
        stored_patient = dict(
            session.scenario.patient
            or {}
        )
        stored_patient_id = (
            str(
                stored_patient.get("mrn")
                or stored_patient.get("id")
                or ""
            ).strip()
            or (
                "evaluation-"
                + safe_slug(
                    session.scenario.scenario_id
                )
            )
        )
        clinical_context_source = (
            "complete_episode_pack"
        )

        base_waveform_source = (
            session.base_waveform_source
            or "unknown"
        )
        capture_source = (
            "hybrid_api_range_injection"
            if is_api_range_source(
                base_waveform_source
            )
            else "hybrid_incart_injection"
        )
        event_end_seconds = (
            event_end_offset
            / rate
        )

        source_segments = [
            {
                "type": "pre_event",
                "source": (
                    base_waveform_source
                ),
                "startSeconds": 0.0,
                "endSeconds": round(
                    reference_onset_seconds,
                    3,
                ),
                "durationSeconds": round(
                    actual_pre,
                    3,
                ),
            },
            {
                "type": "controlled_event",
                "source": (
                    "complete_episode_pack"
                ),
                "startSeconds": round(
                    reference_onset_seconds,
                    3,
                ),
                "endSeconds": round(
                    event_end_seconds,
                    3,
                ),
                "durationSeconds": round(
                    event_end_seconds
                    - reference_onset_seconds,
                    3,
                ),
            },
            {
                "type": "post_event",
                "source": (
                    base_waveform_source
                ),
                "startSeconds": round(
                    event_end_seconds,
                    3,
                ),
                "endSeconds": round(
                    duration,
                    3,
                ),
                "durationSeconds": round(
                    actual_post,
                    3,
                ),
            },
        ]
        api_range_capture = (
            {
                "record": (
                    session
                    .base_waveform_record
                ),
                "sourceSampleRate": (
                    session
                    .base_source_sample_rate
                ),
                "resampledRate": rate,
                "bufferSamples": (
                    session
                    .base_buffer_samples
                ),
                "bufferSeconds": (
                    session
                    .base_buffer_seconds
                ),
                "cursorStart": (
                    session
                    .base_cursor_start
                ),
                "cursorEnd": (
                    session
                    .base_cursor_end
                ),
                "captureMode": (
                    session
                    .api_range_capture_mode
                ),
                "wrappedDuringEvaluation": (
                    session
                    .base_source_wrapped
                ),
                "wrapCount": (
                    session
                    .base_source_wrap_count
                ),
                "snapshotReplayed": (
                    session
                    .base_source_replayed
                ),
                "uniqueContinuousData": (
                    not session
                    .base_source_replayed
                ),
                "interpretation": (
                    "The configured API Range snapshot was replayed "
                    "as the surrounding rhythm."
                    if session
                    .base_source_replayed
                    else (
                        "The capture used one continuous API Range "
                        "without replay."
                    )
                ),
            }
            if is_api_range_source(
                base_waveform_source
            )
            else None
        )

        metadata = {
            "id": episode_id,
            "schemaVersion": (
                "episode-v2"
            ),
            "mode": (
                "evaluation_injection"
            ),
            "isEvaluationEpisode": True,
            "clinicalContextMode":
                "episode_pack_only",
            "evaluationContextMode":
                "episode_pack_only",
            "clinicalContextSource":
                "complete_episode_pack",
            "oracleFhirContextUsed":
                False,
            "epicFhirContextUsed":
                False,
            "displayPatientSource":
                "evaluationScenario.patient",
            "evaluationScenarioId": (
                session.scenario
                .scenario_id
            ),
            "patientId": stored_patient_id,
            "patient": stored_patient,
            "oracleDemo": (
                oracle_demo or None
            ),
            "epicDemo": (
                epic_demo or None
            ),
            "scenarioPatient": (
                session.scenario.patient
            ),
            "record": (
                (
                    "API-RANGE-EVAL-"
                    if is_api_range_source(
                        base_waveform_source
                    )
                    else "INCART-EVAL-"
                )
                + safe_slug(
                    session.scenario
                    .scenario_id
                )
            ),
            "captureSource": (
                capture_source
            ),
            "baseWaveformSource": (
                base_waveform_source
            ),
            "baseWaveformRecord": (
                session
                .base_waveform_record
            ),
            "sourceSegments": (
                source_segments
            ),
            "apiRangeCapture": (
                api_range_capture
            ),
            # Evaluation injection no longer invokes Phase 6. Keep the flag
            # explicit in metadata so saved artifacts cannot be misread later.
            "phase6WindowedAnalysisRequested": False,
            "etiologyV7Enabled": True,
            "evidenceConsistencyPreflightRequested": (
                str(
                    os.getenv(
                        "EVIDENCE_CONSISTENCY_PREFLIGHT_ENABLED",
                        "true",
                    )
                )
                .strip()
                .lower()
                in {
                    "1",
                    "true",
                    "yes",
                    "on",
                }
            ),
            "waveformComposition": {
                "method": (
                    "endpoint-constrained-hermite-v2"
                ),
                "transitionSeconds": (
                    INJECTION_TRANSITION_SECONDS
                ),
                "addsRuntimeDelay": False,
                "valueContinuityConstrained": True,
                "slopeContinuityConstrained": True,
                "crossfadeSignalSummationUsed": False,
                "displayMatchesPersistedMv": True,
                "scenarioPhysicalAmplitudeCalibrated": True,
                "leadCalibration": (
                    session
                    .injection_calibration
                ),
            },
            "loopNumber": 1,
            "state": "CAPTURED",
            "analysisStatus": "pending",
            "autoTriggered": True,
            "triggerSource": trigger_details["source"],
            "triggerMode": trigger_details["triggerMode"],
            "isIndependentDiagnosis": False,
            "policyVersion": (
                "evaluation-injection-v1"
            ),
            "label": (
                session.scenario
                .episode.get("type")
                or "monomorphic_vt"
            ),
            "display": (
                session.scenario
                .display
            ),
            "severity": (
                session.scenario
                .severity
            ),
            "sampleRate": rate,
            "sourceSampleRate": (
                session
                .base_source_sample_rate
                or rate
            ),
            "leadIds": (
                list(
                    DISPLAY_LEADS
                )
            ),
            "leadNames": (
                list(
                    OUTPUT_LEAD_NAMES
                )
            ),
            "captureStartSeconds": 0.0,
            "captureEndSeconds": round(
                duration,
                3,
            ),
            "eventStartSeconds": round(
                reference_onset_seconds,
                3,
            ),
            "eventEndSeconds": round(
                event_end_offset
                / rate,
                3,
            ),
            "eventStartOffsetSeconds": round(
                reference_onset_seconds,
                3,
            ),
            "eventEndOffsetSeconds": round(
                event_end_offset
                / rate,
                3,
            ),
            "durationSeconds": round(
                duration,
                3,
            ),
            "eventDurationSeconds": round(
                (
                    event_end_offset
                    - event_start_offset
                )
                / rate,
                3,
            ),
            "preSecondsCaptured": round(
                actual_pre,
                3,
            ),
            "postSecondsCaptured": round(
                actual_post,
                3,
            ),
            "captureCompleteness": {
                "requestedPreSeconds": (
                    session.pre_seconds
                ),
                "actualPreSeconds": round(
                    actual_pre,
                    3,
                ),
                "preContextComplete": (
                    actual_pre + 0.02
                    >= session.pre_seconds
                ),
                "requestedPostSeconds": (
                    session.post_seconds
                ),
                "actualPostSeconds": round(
                    actual_post,
                    3,
                ),
                "postContextComplete": (
                    actual_post + 0.02
                    >= session.post_seconds
                ),
                "captureComplete": (
                    actual_pre + 0.02
                    >= session.pre_seconds
                    and actual_post + 0.02
                    >= session.post_seconds
                ),
                "captureTruncatedByMaxDuration": False,
                "truncationReasons": [],
            },
            "referenceOnsetOffsetSeconds": round(
                reference_onset_seconds,
                3,
            ),
            "referenceEndOffsetSeconds": round(
                event_end_offset
                / rate,
                3,
            ),
            "detectedTriggerOffsetSeconds": round(
                detected_seconds,
                3,
            ),
            "triggerLatencySeconds": round(
                trigger_latency,
                3,
            ),
            "detectorRuleId": (
                session.detector_rule_id
            ),
            "detectorRateBpm": (
                round(
                    session.detector_rate_bpm,
                    1,
                )
                if session
                .detector_rate_bpm
                is not None
                else None
            ),
            "triggerHeartRate": (
                session.scenario
                .trigger_heart_rate
                or (
                    int(
                        round(
                            session
                            .detector_rate_bpm
                        )
                    )
                    if session
                    .detector_rate_bpm
                    is not None
                    else None
                )
            ),
            "annotationCount": (
                len(
                    all_annotations
                )
            ),
            "annotationCounts": {
                "EVAL_REF": 1,
                trigger_details["symbol"]: 1,
            },
            "normalAnnotationCount": 0,
            "abnormalAnnotationCount": 1,
            "signalQualityAnnotationCount": 0,
            "eventAnnotationCount": 2,
            "eventAnnotationCounts": {
                "EVAL_REF": 1,
                trigger_details["symbol"]: 1,
            },
            "triggerAnnotationCount": 1,
            "triggerAnnotationCounts": {
                trigger_details["symbol"]: 1,
            },
            "triggerCategoryCounts": {
                trigger_details["category"]: 1,
            },
            "triggerAnnotations": (
                trigger_annotations
            ),
            "annotations": (
                all_annotations
            ),
            "referenceAnnotations": (
                [reference_annotation]
            ),
            "referenceFinding": {
                "display": (
                    session.scenario
                    .display
                ),
                "severity": (
                    session.scenario
                    .severity
                ),
                "sourceType": (
                    "evaluation_reference_scenario"
                ),
                "sourceName": (
                    "SLM_Eval"
                ),
                "referenceAnnotation": True,
            },
            "evaluationScenario": {
                "contextMode": (
                    "episode_pack_only"
                ),
                "oracleFhirContextUsed": False,
                "episodeId": (
                    session.scenario
                    .scenario_id
                ),
                "episode": (
                    session.scenario
                    .episode
                ),
                "patient": (
                    session.scenario
                    .patient
                ),
                "vitals": (
                    session.scenario
                    .vitals
                ),
                "labs": (
                    session.scenario
                    .labs
                ),
                "medications": (
                    session.scenario
                    .medications
                ),
                "clinicalContext": (
                    session.scenario
                    .clinical_context
                ),
            },
            "diagnosis": None,
            "provenance": {
                "waveformSource": (
                    (
                        "PhysioNet INCART"
                        if base_waveform_source
                        == "physionet-incart"
                        else (
                            "API Range"
                            if is_api_range_source(
                                base_waveform_source
                            )
                            else base_waveform_source
                        )
                    )
                    + " with controlled "
                    "SLM_Eval waveform injection"
                ),
                "annotationSource": (
                    "evaluation injection "
                    "reference onset and "
                    "deterministic detector"
                ),
                "clinicalContextSource": (
                    clinical_context_source
                ),
                "captureSource": (
                    "exact emitted mixed stream"
                ),
            },
            "capturedAt": now_iso(),
        }

        print(
            "[KGEN EVAL CAPTURE PROVENANCE]",
            {
                "episodeId": episode_id,
                "scenarioId": (
                    session.scenario
                    .scenario_id
                ),
                "baseWaveformSource": (
                    base_waveform_source
                ),
                "baseWaveformRecord": (
                    session
                    .base_waveform_record
                ),
                "sampleRateHz": rate,
                "leadIds": list(
                    DISPLAY_LEADS
                ),
                "sourceSegments": (
                    source_segments
                ),
                "captureComplete": (
                    metadata[
                        "captureCompleteness"
                    ][
                        "captureComplete"
                    ]
                ),
                "apiRangeCapture": (
                    api_range_capture
                ),
            },
            flush=True,
        )

        atomic_json(
            episode_dir
            / "metadata.json",
            metadata,
        )

        atomic_json(
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

        incident = (
            incident_coordinator
            .register_episode(
                metadata
            )
        )

        incident["mode"] = (
            "evaluation_injection"
        )

        incident[
            "evaluationScenarioId"
        ] = (
            session.scenario
            .scenario_id
        )

        incident["patientId"] = stored_patient_id
        incident["patient"] = stored_patient
        incident["clinicalContextMode"] = (
            "episode_pack_only"
        )
        incident["clinicalContextSource"] = (
            "complete_episode_pack"
        )
        incident["oracleFhirContextUsed"] = False
        incident["epicFhirContextUsed"] = False
        incident["displayPatientSource"] = (
            "evaluationScenario.patient"
        )
        incident["oracleDemo"] = oracle_demo or None
        incident["epicDemo"] = epic_demo or None

        incident.setdefault(
            "provenance",
            {},
        )[
            "clinicalContextSource"
        ] = clinical_context_source

        incident_coordinator.write_json(
            incident_coordinator
            .incident_file(
                incident["id"]
            ),
            incident,
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

        atomic_json(
            episode_dir
            / "metadata.json",
            metadata,
        )

        context = (
            self._clinical_context(
                session,
                incident["id"],
                episode_id,
            )
        )

        clinical_context_service.save(
            incident["id"],
            context,
        )

        return metadata

    def _clinical_context(
        self,
        session: InjectionSession,
        incident_id: str,
        episode_id: str,
    ) -> dict[str, Any]:
        patient = (
            session.scenario.patient
        )

        captured_at = now_iso()

        lab_labels = {
            "glucose": "Glucose",
            "potassium": "Potassium",
            "creatinine": "Creatinine",
            "wbc": "WBC",
            "whiteBloodCellCount": "WBC",
            "troponinT": "Troponin T",
            "bnp": "BNP",
            "magnesium": "Magnesium",
        }

        vital_labels = {
            "heartRate": "Heart rate",
            "heartRateBpm": "Heart rate",
            "respiratoryRate": "Respiratory rate",
            "respiratoryRateBpm": "Respiratory rate",
            "spo2": "SpO2",
            "spo2Pct": "SpO2",
            "temperature": "Temperature",
            "temperatureC": "Temperature",
            "systolic": "Systolic blood pressure",
            "diastolic": "Diastolic blood pressure",
            "meanArterialPressure": "Mean arterial pressure",
            "map": "Mean arterial pressure",
        }

        def trend_color(
            entry: Any,
        ) -> str:
            if not isinstance(
                entry,
                dict,
            ):
                return "blue"

            flag = str(
                entry.get("flag")
                or entry.get("status")
                or ""
            ).lower()

            if any(
                token in flag
                for token in (
                    "critical",
                    "high",
                    "low",
                    "abnormal",
                    "red",
                )
            ):
                return "red"

            if any(
                token in flag
                for token in (
                    "warning",
                    "yellow",
                )
            ):
                return "yellow"

            return "blue"

        def trend_row(
            *,
            field: str,
            label: str,
            entry: Any,
            resource_prefix: str,
        ) -> dict[str, Any]:
            value, unit = value_and_unit(
                entry
            )

            point = {
                "resourceId": (
                    f"{resource_prefix}-{safe_slug(field)}"
                ),
                "value": value,
                "unit": unit,
                "observedAt": captured_at,
                "status": "scenario",
                "minutesFromAnchor": 0.0,
                "relation": "during_episode",
                "relationLabel": "episode time",
                "temporalBucket": "episode_near",
            }

            return {
                "field": field,
                "label": label,
                "latestValue": value,
                "unit": unit,
                "latestAt": captured_at,
                "latestRelation": "during_episode",
                "latestRelationLabel": "episode time",
                "trendDirection": "insufficient_data",
                "color": trend_color(entry),
                "classification": trend_color(entry),
                "temporalBucket": "episode_near",
                "points": [point],
            }

        labs = [
            trend_row(
                field=str(name),
                label=(
                    lab_labels.get(str(name))
                    or str(name)
                    .replace("_", " ")
                    .title()
                ),
                entry=entry,
                resource_prefix="eval-lab",
            )
            for name, entry
            in session.scenario.labs.items()
        ]

        vitals: list[dict[str, Any]] = []
        scenario_vitals = dict(
            session.scenario.vitals
        )

        blood_pressure = (
            scenario_vitals.get(
                "bloodPressure"
            )
            or scenario_vitals.get("bp")
            or {}
        )

        for key in (
            "systolic",
            "diastolic",
            "meanArterialPressure",
            "map",
        ):
            if (
                key not in scenario_vitals
                and isinstance(
                    blood_pressure,
                    dict,
                )
                and key in blood_pressure
            ):
                scenario_vitals[key] = (
                    blood_pressure[key]
                )

        for name, entry in (
            scenario_vitals.items()
        ):
            if (
                name in {
                    "bloodPressure",
                    "bp",
                    "note",
                    "bloodPressureNote",
                }
                or isinstance(entry, dict)
                and value_and_unit(entry)[0]
                is None
            ):
                continue

            vitals.append(
                trend_row(
                    field=str(name),
                    label=(
                        vital_labels.get(
                            str(name)
                        )
                        or str(name)
                        .replace("_", " ")
                        .title()
                    ),
                    entry=entry,
                    resource_prefix=(
                        "eval-vital"
                    ),
                )
            )

        medications = (
            session.scenario
            .medications
        )

        if not isinstance(
            medications,
            list,
        ):
            medications = []

        medication_timeline = []

        for index, item in enumerate(
            medications
        ):
            if isinstance(item, str):
                name = item
                dose = None
                route = None
                status = "listed"
            elif isinstance(
                item,
                dict,
            ):
                name = (
                    item.get("name")
                    or item.get(
                        "medication"
                    )
                    or item.get("drug")
                    or "Medication"
                )

                dose = (
                    item.get(
                        "doseDisplay"
                    )
                    or item.get("dose")
                )

                route = item.get("route")
                status = (
                    item.get("status")
                    or "listed"
                )
            else:
                continue

            medication_timeline.append(
                {
                    "id": (
                        f"eval-med-{index}"
                    ),
                    "resourceType": (
                        "Medication"
                    ),
                    "name": name,
                    "doseDisplay": dose,
                    "route": route,
                    "status": status,
                    "eventTime": captured_at,
                    "minutesFromAnchor": 0.0,
                    "relation": (
                        "during_episode"
                    ),
                    "relationLabel": (
                        "episode context"
                    ),
                    "temporalBucket": (
                        "episode_near"
                    ),
                    "evidenceLevel": (
                        "scenario_context"
                    ),
                }
            )

        conditions = [
            {
                "id": (
                    f"eval-condition-{index}"
                ),
                "name": item,
                "clinicalStatus": "active",
                "verificationStatus": (
                    "scenario"
                ),
            }
            for index, item
            in enumerate(
                patient.get("history")
                or []
            )
            if isinstance(item, str)
        ]

        return {
            "schemaVersion": (
                "clinical-context-v1"
            ),
            "incidentId": incident_id,
            "storedWithEpisodeId": (
                episode_id
            ),
            "status": "ready",
            "contextAnchor": {
                "value": captured_at,
                "basis": (
                    "evaluation_capture"
                ),
            },
            "patientSummary": {
                "id": (
                    patient.get("mrn")
                    or episode_id
                ),
                "name": (
                    patient.get("name")
                ),
                "birthDate": (
                    patient.get("dob")
                ),
                "sex": (
                    patient.get("sex")
                ),
                "gender": (
                    patient.get("sex")
                ),
                "primaryDiagnosis": (
                    patient.get(
                        "primaryDiagnosis"
                    )
                ),
                "history": (
                    patient.get(
                        "history"
                    )
                    or []
                ),
            },
            "labTrends": labs,
            "vitalTrends": vitals,
            "medicationTimeline": (
                medication_timeline
            ),
            "conditions": conditions,
            "encounters": [],
            "diagnosticReports": [],
            "documents": [],
            "scenarioClinicalContext": (
                session.scenario
                .clinical_context
            ),
            "dataQuality": {
                "fallbackUsed": False,
                "observationCount": (
                    len(labs)
                    + len(vitals)
                ),
                "targetedObservationCount": (
                    len(labs)
                ),
                "matchedLabCount": len(labs),
                "matchedVitalCount": (
                    len(vitals)
                ),
                "medicationCount": (
                    len(
                        medication_timeline
                    )
                ),
                "conditionCount": (
                    len(conditions)
                ),
                "encounterCount": 0,
                "patientLoaded": True,
                "diagnosticReportCount": 0,
                "documentCount": 0,
            },
            "limitations": [],
            "provenance": {
                "source": (
                    "complete_episode_pack"
                ),
                "mode": (
                    "evaluation_injection"
                ),
                "clinicalContextMode": (
                    "episode_pack_only"
                ),
                "oracleFhirContextUsed": False,
            },
        }

    def _score_phase7(
        self,
        *,
        scenario_id: str,
        phase7_result: dict[str, Any],
    ) -> dict[str, Any] | None:
        slm_result = (
            phase7_result.get(
                "slmResponse"
            )
            or {}
        )

        content = slm_result.get(
            "content"
        )

        if not content:
            return None

        parsed = self._parse_json(
            content
        )

        if not isinstance(
            parsed,
            dict,
        ):
            return {
                "status": "not_scored",
                "reason": (
                    "The Phase 7 SLM response "
                    "was not structured JSON."
                ),
            }

        try:
            from app.evaluation.repository import (
                load_answer_key,
            )
            from app.evaluation.scorer import (
                score_response,
            )

            normalized_response = (
                self._phase7_to_evaluation_response(
                    parsed
                )
            )

            result = score_response(
                episode_id=scenario_id,
                model_response=(
                    normalized_response
                ),
                answer_key=load_answer_key(),
            )

            result[
                "normalizedModelResponse"
            ] = normalized_response

            return result

        except Exception as error:
            return {
                "status": "not_scored",
                "reason": (
                    f"{type(error).__name__}: "
                    f"{error}"
                ),
            }

    @staticmethod
    def _phase7_to_evaluation_response(
        parsed: dict[str, Any],
    ) -> dict[str, Any]:
        widget = (
            parsed.get(
                "widgetInterpretation"
            )
            or {}
        )

        current = (
            widget.get(
                "currentSituation"
            )
            or {}
        )

        root_cause = (
            parsed.get(
                "rootCauseAssessment"
            )
            or {}
        )

        contributors: list[str] = []

        for item in (
            widget.get(
                "importantFindings"
            )
            or []
        ):
            if isinstance(item, str):
                contributors.append(item)

        for item in (
            widget.get(
                "possibleContributors"
            )
            or root_cause.get(
                "candidates"
            )
            or []
        ):
            if isinstance(item, str):
                contributors.append(item)
                continue

            if not isinstance(item, dict):
                continue

            text = (
                item.get("title")
                or item.get("name")
                or item.get(
                    "conclusion"
                )
                or item.get(
                    "hypothesis"
                )
            )

            if text:
                contributors.append(
                    str(text)
                )

        actions: list[str] = []

        for item in (
            parsed.get(
                "recommendedClinicalReview"
            )
            or parsed.get(
                "suggestedClinicalReview"
            )
            or widget.get(
                "recommendedNextChecks"
            )
            or []
        ):
            if isinstance(item, str):
                actions.append(item)
                continue

            if isinstance(item, dict):
                action = (
                    item.get("action")
                    or item.get("title")
                    or item.get("check")
                )

                if action:
                    actions.append(
                        str(action)
                    )

        uncertainty: list[str] = []

        for item in (
            widget.get(
                "importantLimitations"
            )
            or []
        ):
            if isinstance(item, str):
                uncertainty.append(item)

        for item in (
            parsed.get(
                "contradictionsAndUncertainty"
            )
            or []
        ):
            if isinstance(item, str):
                uncertainty.append(item)
            elif isinstance(item, dict):
                text = (
                    item.get("detail")
                    or item.get("reason")
                    or item.get("description")
                )
                if text:
                    uncertainty.append(
                        str(text)
                    )

        for item in (
            parsed.get(
                "missingEvidence"
            )
            or []
        ):
            if isinstance(item, str):
                uncertainty.append(item)
            elif isinstance(item, dict):
                text = (
                    item.get("reason")
                    or item.get(
                        "evidenceType"
                    )
                    or item.get("detail")
                )
                if text:
                    uncertainty.append(
                        str(text)
                    )

        clinical_context = (
            current.get("narrative")
            or widget.get(
                "currentSituationNarrative"
            )
            or parsed.get(
                "clinicalContext"
            )
            or (
                "; ".join(
                    str(item)
                    for item in (
                        parsed.get(
                            "clinicallyRelevantContext"
                        )
                        or []
                    )
                    if item
                )
            )
            or ""
        )

        most_likely = (
            widget.get(
                "rootCauseNarrative"
            )
            or root_cause.get(
                "conclusion"
            )
            or parsed.get(
                "mostLikelyEtiology"
            )
            or ""
        )

        return {
            "episodeSummary": (
                widget.get(
                    "episodeNarrative"
                )
                or parsed.get(
                    "episodeSummary"
                )
                or parsed.get(
                    "evidenceSummary"
                )
                or ""
            ),
            "rhythmInterpretation": (
                widget.get(
                    "arrhythmiaNarrative"
                )
                or parsed.get(
                    "rhythmInterpretation"
                )
                or (
                    "; ".join(
                        str(item)
                        for item in (
                            parsed.get(
                                "ecgFindings"
                            )
                            or []
                        )
                        if item
                    )
                )
                or ""
            ),
            "clinicalContext": (
                clinical_context
            ),
            "mostLikelyEtiology": (
                most_likely
            ),
            "contributingFactors": list(
                dict.fromkeys(
                    contributors
                )
            ),
            "recommendedImmediateActions": list(
                dict.fromkeys(actions)
            ),
            "uncertaintyAndMissingData": list(
                dict.fromkeys(
                    uncertainty
                )
            ),
        }

    @staticmethod
    def _parse_json(
        value: Any,
    ) -> dict[str, Any] | None:
        if isinstance(
            value,
            dict,
        ):
            return value

        if not isinstance(
            value,
            str,
        ):
            return None

        text = value.strip()

        if text.startswith("```"):
            text = re.sub(
                r"^```(?:json)?\s*",
                "",
                text,
                flags=re.IGNORECASE,
            )

            text = re.sub(
                r"\s*```$",
                "",
                text,
            )

        try:
            parsed = json.loads(
                text
            )

            return (
                parsed
                if isinstance(
                    parsed,
                    dict,
                )
                else None
            )

        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")

            if (
                start < 0
                or end <= start
            ):
                return None

            try:
                parsed = json.loads(
                    text[
                        start:
                        end + 1
                    ]
                )

                return (
                    parsed
                    if isinstance(
                        parsed,
                        dict,
                    )
                    else None
                )

            except json.JSONDecodeError:
                return None

    def _completion_message(
        self,
        session: InjectionSession,
    ) -> str:
        parts = [
            session.scenario.display,
            (
                f"latency "
                f"{session.public_status().get('triggerLatencySeconds')}s"
            ),
            "20-second capture complete",
        ]

        if isinstance(
            session.score,
            dict,
        ):
            total = (
                session.score.get(
                    "total"
                )
            )

            if total is not None:
                parts.append(
                    f"score {total}/100"
                )

        return " • ".join(parts)

    @staticmethod
    def publish(
        event: dict[str, Any],
    ) -> None:
        episode_coordinator.publish(
            event
        )


evaluation_injection_service = (
    EvaluationInjectionService()
)