from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


class AnalysisInputError(ValueError):
    """Expected invalid waveform input that maps to HTTP 422."""

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.details = details or {}


@dataclass(frozen=True)
class EpisodeInput:
    episode_id: str
    episode_dir: Path
    metadata_path: Path
    waveform_path: Path
    analysis_path: Path
    metadata: dict[str, Any]
    sampling_rate_hz: float
    lead_ids: list[str]
    lead_names: list[str]
    waveforms_mv: dict[str, np.ndarray]
    original_lengths: dict[str, int]
    aligned_sample_count: int
    fingerprint: str
    validation: dict[str, Any]


@dataclass(frozen=True)
class PreprocessedLead:
    lead_id: str
    signal_mv: np.ndarray
    filter_details: dict[str, Any]


@dataclass(frozen=True)
class BeatWindow:
    beat_index: int
    r_peak_sample: int
    start_sample: int
    end_sample: int
    complete: bool
    invalid_percent_by_lead: dict[str, float]