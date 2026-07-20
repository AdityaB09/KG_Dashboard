from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.analysis.constants import (
    ALGORITHM_VERSION,
    EXPECTED_ECG_LEADS,
    MAX_SAMPLING_RATE_HZ,
    MIN_SAMPLING_RATE_HZ,
)
from app.analysis.models import (
    AnalysisInputError,
    EpisodeInput,
)
from app.config import settings


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as error:
        raise AnalysisInputError(
            f"Invalid JSON file: {path.name}",
            details={
                "path": str(path),
                "error": str(error),
            },
        ) from error

    if not isinstance(value, dict):
        raise AnalysisInputError(
            f"Expected an object in {path.name}.",
            details={"path": str(path)},
        )

    return value


def write_json_atomic(
    path: Path,
    payload: dict[str, Any],
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
            payload,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    temporary.replace(path)


def _metadata_for_fingerprint(
    metadata: dict[str, Any],
) -> dict[str, Any]:
    keys = (
        "id",
        "schemaVersion",
        "record",
        "loopNumber",
        "sampleRate",
        "sourceSampleRate",
        "leadIds",
        "leadNames",
        "captureStartSeconds",
        "captureEndSeconds",
        "eventStartSeconds",
        "eventEndSeconds",
        "eventStartOffsetSeconds",
        "eventEndOffsetSeconds",
        "durationSeconds",
        "triggerAnnotations",
        "annotations",
        "captureCompleteness",
    )

    return {
        key: metadata.get(key)
        for key in keys
    }


def input_fingerprint(
    metadata: dict[str, Any],
    waveform_path: Path,
) -> str:
    digest = hashlib.sha256()

    digest.update(
        json.dumps(
            _metadata_for_fingerprint(
                metadata
            ),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )

    with waveform_path.open("rb") as source:
        while True:
            chunk = source.read(
                1024 * 1024
            )

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def _string_list(
    value: np.ndarray,
    field: str,
) -> list[str]:
    array = np.asarray(value)

    if array.ndim != 1:
        raise AnalysisInputError(
            f"{field} must be one-dimensional.",
            details={
                "field": field,
                "shape": list(array.shape),
            },
        )

    return [
        str(item)
        for item in array.tolist()
    ]


def _matrix_to_leads(
    matrix: np.ndarray,
    lead_ids: list[str],
    field: str,
) -> tuple[
    dict[str, np.ndarray],
    dict[str, int],
]:
    if not np.issubdtype(
        matrix.dtype,
        np.number,
    ):
        raise AnalysisInputError(
            f"{field} must contain numeric values.",
            details={
                "field": field,
                "dtype": str(matrix.dtype),
            },
        )

    if matrix.ndim == 1:
        if len(lead_ids) != 1:
            raise AnalysisInputError(
                (
                    f"{field} is one-dimensional "
                    "but multiple leads are declared."
                ),
                details={
                    "shape": list(matrix.shape),
                    "leadIds": lead_ids,
                },
            )

        matrix = matrix[:, None]

    if matrix.ndim != 2:
        raise AnalysisInputError(
            (
                f"{field} must be a "
                "sample-by-lead matrix."
            ),
            details={
                "field": field,
                "shape": list(matrix.shape),
            },
        )

    if matrix.shape[1] == len(lead_ids):
        samples_by_lead = matrix
    elif matrix.shape[0] == len(lead_ids):
        samples_by_lead = matrix.T
    else:
        raise AnalysisInputError(
            (
                f"{field} shape does not "
                "match lead_ids."
            ),
            details={
                "shape": list(matrix.shape),
                "leadCount": len(lead_ids),
            },
        )

    output: dict[str, np.ndarray] = {}
    lengths: dict[str, int] = {}

    for index, lead_id in enumerate(
        lead_ids
    ):
        signal = np.asarray(
            samples_by_lead[:, index],
            dtype=np.float64,
        ).copy()

        if signal.ndim != 1:
            raise AnalysisInputError(
                (
                    f"Lead {lead_id} is not "
                    "one-dimensional."
                ),
                details={
                    "leadId": lead_id,
                    "shape": list(
                        signal.shape
                    ),
                },
            )

        output[lead_id] = signal
        lengths[lead_id] = int(
            signal.size
        )

    return output, lengths


def _per_lead_invalid(
    signal: np.ndarray,
) -> dict[str, Any]:
    nan_count = int(
        np.isnan(signal).sum()
    )

    positive_infinity_count = int(
        np.isposinf(signal).sum()
    )

    negative_infinity_count = int(
        np.isneginf(signal).sum()
    )

    invalid_count = (
        nan_count
        + positive_infinity_count
        + negative_infinity_count
    )

    return {
        "sampleCount": int(signal.size),
        "nanCount": nan_count,
        "positiveInfinityCount": (
            positive_infinity_count
        ),
        "negativeInfinityCount": (
            negative_infinity_count
        ),
        "invalidSampleCount": (
            invalid_count
        ),
        "invalidSamplePercent": round(
            100.0
            * invalid_count
            / max(signal.size, 1),
            6,
        ),
    }


def load_episode_input(
    episode_id: str,
) -> EpisodeInput:
    episode_dir = (
        Path(
            settings.EPISODE_STORAGE_PATH
        )
        / episode_id
    )

    metadata_path = (
        episode_dir / "metadata.json"
    )

    waveform_path = (
        episode_dir / "waveforms.npz"
    )

    analysis_path = (
        episode_dir / "analysis.json"
    )

    if (
        not episode_dir.exists()
        or not metadata_path.exists()
    ):
        raise FileNotFoundError(
            episode_id
        )

    if not waveform_path.exists():
        raise AnalysisInputError(
            "Episode waveform file is missing.",
            details={
                "episodeId": episode_id,
                "path": str(waveform_path),
            },
        )

    metadata = read_json(metadata_path)
    stored_id = str(
        metadata.get("id") or ""
    )

    if stored_id != episode_id:
        raise AnalysisInputError(
            (
                "Episode ID does not match "
                "metadata."
            ),
            details={
                "requestedEpisodeId": (
                    episode_id
                ),
                "metadataEpisodeId": stored_id,
            },
        )

    try:
        with np.load(
            waveform_path,
            allow_pickle=False,
        ) as data:
            keys = sorted(data.files)

            if (
                "lead_ids" not in data
                or "sample_rate" not in data
            ):
                raise AnalysisInputError(
                    (
                        "waveforms.npz is missing "
                        "required identity fields."
                    ),
                    details={
                        "required": [
                            "lead_ids",
                            "sample_rate",
                        ],
                        "keys": keys,
                    },
                )

            if "centered_mv" in data:
                signal_key = "centered_mv"
            elif "raw_mv" in data:
                signal_key = "raw_mv"
            else:
                signal_key = (
                    "per_lead_arrays"
                )

            lead_ids = _string_list(
                data["lead_ids"],
                "lead_ids",
            )

            lead_names = (
                _string_list(
                    data["lead_names"],
                    "lead_names",
                )
                if "lead_names" in data
                else list(lead_ids)
            )

            sample_rate_array = np.asarray(
                data["sample_rate"]
            )

            if sample_rate_array.size != 1:
                raise AnalysisInputError(
                    (
                        "sample_rate must be "
                        "scalar."
                    ),
                    details={
                        "shape": list(
                            sample_rate_array.shape
                        )
                    },
                )

            sampling_rate_hz = float(
                sample_rate_array
                .reshape(-1)[0]
            )

            if (
                signal_key
                == "per_lead_arrays"
            ):
                available = [
                    lead
                    for lead in lead_ids
                    if lead in data
                ]

                if not available:
                    raise AnalysisInputError(
                        (
                            "waveforms.npz contains "
                            "no ECG waveform matrix "
                            "or per-lead arrays."
                        ),
                        details={
                            "expectedAnyOf": [
                                "centered_mv",
                                "raw_mv",
                                *lead_ids,
                            ],
                            "keys": keys,
                        },
                    )

                waveforms = {}
                lengths = {}

                for lead_id in available:
                    array = np.asarray(
                        data[lead_id]
                    )

                    if not np.issubdtype(
                        array.dtype,
                        np.number,
                    ):
                        raise AnalysisInputError(
                            (
                                f"Lead {lead_id} "
                                "must contain "
                                "numeric values."
                            ),
                            details={
                                "leadId": lead_id,
                                "dtype": str(
                                    array.dtype
                                ),
                            },
                        )

                    if array.ndim != 1:
                        raise AnalysisInputError(
                            (
                                f"Lead {lead_id} "
                                "must be "
                                "one-dimensional."
                            ),
                            details={
                                "leadId": lead_id,
                                "shape": list(
                                    array.shape
                                ),
                            },
                        )

                    waveforms[lead_id] = (
                        np.asarray(
                            array,
                            dtype=np.float64,
                        ).copy()
                    )

                    lengths[lead_id] = int(
                        array.size
                    )

                lead_ids = available

                lead_names = [
                    (
                        lead_names[index]
                        if index
                        < len(lead_names)
                        else lead
                    )
                    for index, lead
                    in enumerate(available)
                ]

            else:
                matrix = np.asarray(
                    data[signal_key]
                )

                (
                    waveforms,
                    lengths,
                ) = _matrix_to_leads(
                    matrix,
                    lead_ids,
                    signal_key,
                )

    except AnalysisInputError:
        raise
    except (
        OSError,
        ValueError,
        KeyError,
    ) as error:
        raise AnalysisInputError(
            (
                "waveforms.npz could "
                "not be read."
            ),
            details={
                "episodeId": episode_id,
                "error": str(error),
            },
        ) from error

    if (
        not np.isfinite(
            sampling_rate_hz
        )
        or not (
            MIN_SAMPLING_RATE_HZ
            <= sampling_rate_hz
            <= MAX_SAMPLING_RATE_HZ
        )
    ):
        raise AnalysisInputError(
            (
                "Sampling rate is invalid "
                "or outside the supported "
                "range."
            ),
            details={
                "samplingRateHz": (
                    sampling_rate_hz
                )
            },
        )

    metadata_rate = metadata.get(
        "sampleRate"
    )

    if (
        metadata_rate is not None
        and abs(
            float(metadata_rate)
            - sampling_rate_hz
        )
        > 1e-6
    ):
        raise AnalysisInputError(
            (
                "Sampling rate differs "
                "between metadata.json "
                "and waveforms.npz."
            ),
            details={
                "metadataSampleRate": (
                    metadata_rate
                ),
                "npzSampleRate": (
                    sampling_rate_hz
                ),
            },
        )

    if (
        not lead_ids
        or len(set(lead_ids))
        != len(lead_ids)
    ):
        raise AnalysisInputError(
            (
                "lead_ids must be "
                "non-empty and unique."
            ),
            details={
                "leadIds": lead_ids
            },
        )

    metadata_leads = [
        str(value)
        for value
        in metadata.get(
            "leadIds"
        )
        or []
    ]

    missing_from_npz = [
        lead
        for lead in metadata_leads
        if lead not in waveforms
    ]

    unexpected_leads = [
        lead
        for lead in lead_ids
        if lead
        not in EXPECTED_ECG_LEADS
    ]

    minimum_length = min(
        lengths.values()
    ) if lengths else 0

    maximum_length = max(
        lengths.values()
    ) if lengths else 0

    if minimum_length <= 0:
        raise AnalysisInputError(
            "No waveform samples are available."
        )

    if minimum_length != maximum_length:
        waveforms = {
            lead: signal[
                :minimum_length
            ].copy()
            for lead, signal
            in waveforms.items()
        }

    invalid_by_lead = {
        lead: _per_lead_invalid(
            signal
        )
        for lead, signal
        in waveforms.items()
    }

    validation = {
        "status": "ready",
        "episodeIdVerified": True,
        "samplingRateVerified": True,
        "waveformFile": (
            "waveforms.npz"
        ),
        "waveformArrayKey": (
            signal_key
        ),
        "npzKeys": keys,
        "declaredLeadIds": (
            metadata_leads
        ),
        "loadedLeadIds": lead_ids,
        "missingDeclaredLeadIds": (
            missing_from_npz
        ),
        "unexpectedLeadIds": (
            unexpected_leads
        ),
        "equalLeadLengths": (
            minimum_length
            == maximum_length
        ),
        "originalLeadLengths": (
            lengths
        ),
        "alignedSampleCount": (
            minimum_length
        ),
        "invalidSamplesByLead": (
            invalid_by_lead
        ),
        "rawWaveformModified": False,
    }

    return EpisodeInput(
        episode_id=episode_id,
        episode_dir=episode_dir,
        metadata_path=metadata_path,
        waveform_path=waveform_path,
        analysis_path=analysis_path,
        metadata=metadata,
        sampling_rate_hz=(
            sampling_rate_hz
        ),
        lead_ids=lead_ids,
        lead_names=lead_names,
        waveforms_mv=waveforms,
        original_lengths=lengths,
        aligned_sample_count=(
            minimum_length
        ),
        fingerprint=input_fingerprint(
            metadata,
            waveform_path,
        ),
        validation=validation,
    )


def reusable_analysis(
    path: Path,
    fingerprint: str,
) -> dict[str, Any] | None:
    if not path.exists():
        return None

    try:
        saved = read_json(path)
    except AnalysisInputError:
        return None

    if (
        saved.get(
            "inputFingerprint"
        )
        == fingerprint
        and saved.get(
            "algorithmVersion"
        )
        == ALGORITHM_VERSION
        and saved.get("status")
        in {"ready", "partial"}
    ):
        return saved

    return None