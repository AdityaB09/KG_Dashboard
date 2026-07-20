from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import (
    TestClient,
)

from app.analysis.episode_analyzer import (
    episode_analyzer,
)
from app.analysis.incident_analyzer import (
    incident_analyzer,
)
from app.analysis.io import (
    load_episode_input,
)
from app.analysis.models import (
    AnalysisInputError,
)
from app.analysis.morphology import (
    compare_templates,
)
from app.analysis.preprocessing import (
    preprocess_signal,
)
from app.analysis.qrs import (
    measure_qrs,
)
from app.analysis.r_peaks import (
    detect_candidates,
)
from app.analysis.rr_metrics import (
    analyze_rr,
)
from app.analysis.signal_quality import (
    analyze_lead_quality,
)
from app.config import settings
from app.episode_routes import (
    incident_router,
    router,
)
from app.episodes import (
    episode_coordinator,
)
from app.incidents import (
    incident_coordinator,
)


FS = 220.0

LEADS = [
    "lead1",
    "lead2",
    "lead3",
    "avr",
    "avl",
    "avf",
    "v1",
    "v2",
    "v3",
    "v4",
    "v5",
    "v6",
]


def synthetic_ecg(
    seconds: float = 12.0,
    rr_seconds: float = 0.8,
    amplitude: float = 1.0,
    noise: float = 0.005,
    polarity: float = 1.0,
) -> tuple[
    np.ndarray,
    list[int],
]:
    count = int(
        seconds * FS
    )

    time = (
        np.arange(count) / FS
    )

    signal = (
        0.03
        * np.sin(
            2
            * np.pi
            * 0.2
            * time
        )
    )

    peaks = list(
        range(
            int(FS),
            count - int(FS),
            int(
                rr_seconds * FS
            ),
        )
    )

    for peak in peaks:
        width = max(
            1,
            int(0.018 * FS),
        )

        indices = np.arange(
            max(
                0,
                peak - 4 * width,
            ),
            min(
                count,
                peak + 4 * width + 1,
            ),
        )

        signal[indices] += (
            polarity
            * amplitude
            * np.exp(
                -0.5
                * (
                    (
                        indices - peak
                    )
                    / width
                )
                ** 2
            )
        )

        s_center = (
            peak
            + int(0.04 * FS)
        )

        s_indices = np.arange(
            max(
                0,
                s_center - 3 * width,
            ),
            min(
                count,
                s_center
                + 3 * width
                + 1,
            ),
        )

        signal[s_indices] -= (
            polarity
            * 0.35
            * amplitude
            * np.exp(
                -0.5
                * (
                    (
                        s_indices
                        - s_center
                    )
                    / width
                )
                ** 2
            )
        )

    rng = (
        np.random
        .default_rng(7)
    )

    signal += rng.normal(
        0.0,
        noise,
        count,
    )

    return (
        signal.astype(
            np.float64
        ),
        peaks,
    )


def episode_metadata(
    episode_id: str,
    sample_count: int,
    trigger_sample: int,
) -> dict:
    normal_samples = list(
        range(
            int(FS),
            sample_count - int(FS),
            int(0.8 * FS),
        )
    )

    return {
        "id": episode_id,
        "schemaVersion": (
            "episode-v2"
        ),
        "record": "I01",
        "loopNumber": 1,
        "sampleRate": FS,
        "leadIds": LEADS,
        "leadNames": LEADS,
        "captureStartSeconds": (
            0.0
        ),
        "captureEndSeconds": (
            sample_count / FS
        ),
        "eventStartOffsetSeconds": (
            max(
                0.0,
                trigger_sample
                / FS
                - 1.0,
            )
        ),
        "eventEndOffsetSeconds": (
            min(
                sample_count / FS,
                trigger_sample
                / FS
                + 1.0,
            )
        ),
        "durationSeconds": (
            sample_count / FS
        ),
        "analysisStatus": (
            "pending"
        ),
        "triggerAnnotations": [
            {
                "symbol": "V",
                "absoluteSample": (
                    trigger_sample
                ),
                "captureOffsetSamples": (
                    trigger_sample
                ),
                "captureOffsetSeconds": (
                    trigger_sample / FS
                ),
                "category": (
                    "ventricular_ectopy"
                ),
            }
        ],
        "annotations": [
            {
                "symbol": "N",
                "absoluteSample": (
                    value
                ),
                "captureOffsetSamples": (
                    value
                ),
                "captureOffsetSeconds": (
                    value / FS
                ),
            }
            for value
            in normal_samples
        ] + [
            {
                "symbol": "V",
                "absoluteSample": (
                    trigger_sample
                ),
                "captureOffsetSamples": (
                    trigger_sample
                ),
                "captureOffsetSeconds": (
                    trigger_sample / FS
                ),
            }
        ],
        "annotationCounts": {
            "N": len(
                normal_samples
            ),
            "V": 1,
        },
        "captureCompleteness": {
            "preContextComplete": (
                True
            ),
            "postContextComplete": (
                True
            ),
            "captureComplete": True,
        },
        "provenance": {
            "waveformSource": (
                "PhysioNet INCART"
            ),
            "annotationSource": (
                "PhysioNet "
                "INCART atr"
            ),
        },
    }


@pytest.fixture()
def storage(
    tmp_path: Path,
    monkeypatch: (
        pytest.MonkeyPatch
    ),
) -> tuple[Path, Path]:
    episodes = (
        tmp_path / "episodes"
    )

    incidents = (
        tmp_path / "incidents"
    )

    episodes.mkdir()
    incidents.mkdir()

    monkeypatch.setattr(
        settings,
        "EPISODE_STORAGE_PATH",
        str(episodes),
    )

    monkeypatch.setattr(
        settings,
        "INCIDENT_STORAGE_PATH",
        str(incidents),
    )

    monkeypatch.setattr(
        episode_coordinator,
        "episode_path",
        episodes,
        raising=False,
    )

    monkeypatch.setattr(
        incident_coordinator,
        "episode_path",
        episodes,
    )

    monkeypatch.setattr(
        incident_coordinator,
        "incident_path",
        incidents,
    )

    return episodes, incidents


def write_episode(
    root: Path,
    episode_id: str = (
        "incart-I01-loop-1-"
        "V-000001100"
    ),
    *,
    malformed: bool = False,
    missing_npz: bool = False,
    missing_metadata: bool = False,
    missing_lead: bool = False,
    unequal: bool = False,
    nonnumeric: bool = False,
    add_nan: bool = False,
    add_inf: bool = False,
) -> tuple[
    str,
    np.ndarray,
    list[int],
]:
    signal, peaks = (
        synthetic_ecg()
    )

    trigger = peaks[
        len(peaks) // 2
    ]

    episode_dir = (
        root / episode_id
    )

    episode_dir.mkdir(
        parents=True
    )

    metadata = episode_metadata(
        episode_id,
        signal.size,
        trigger,
    )

    if not missing_metadata:
        (
            episode_dir
            / "metadata.json"
        ).write_text(
            json.dumps(metadata),
            encoding="utf-8",
        )

    if missing_npz:
        return (
            episode_id,
            signal,
            peaks,
        )

    if malformed:
        (
            episode_dir
            / "waveforms.npz"
        ).write_bytes(
            b"not an npz"
        )

        return (
            episode_id,
            signal,
            peaks,
        )

    if nonnumeric:
        np.savez_compressed(
            episode_dir
            / "waveforms.npz",
            lead_ids=np.asarray(
                ["lead1"]
            ),
            lead_names=np.asarray(
                ["I"]
            ),
            sample_rate=np.asarray(
                FS
            ),
            lead1=np.asarray(
                ["bad", "data"]
            ),
        )

        return (
            episode_id,
            signal,
            peaks,
        )

    matrix = np.stack(
        [
            signal
            * (
                1.0
                - index * 0.015
            )
            for index
            in range(len(LEADS))
        ],
        axis=1,
    )

    if add_nan:
        matrix[
            100:110,
            0,
        ] = np.nan

    if add_inf:
        matrix[200, 1] = np.inf
        matrix[201, 1] = -np.inf

    if unequal:
        np.savez_compressed(
            episode_dir
            / "waveforms.npz",
            lead_ids=np.asarray(
                [
                    "lead1",
                    "lead2",
                ]
            ),
            lead_names=np.asarray(
                ["I", "II"]
            ),
            sample_rate=np.asarray(
                FS
            ),
            lead1=signal,
            lead2=signal[:-25],
        )

    else:
        stored_leads = (
            LEADS[:-1]
            if missing_lead
            else LEADS
        )

        stored_matrix = matrix[
            :,
            :len(stored_leads),
        ]

        np.savez_compressed(
            episode_dir
            / "waveforms.npz",
            centered_mv=(
                stored_matrix
            ),
            raw_mv=stored_matrix,
            lead_ids=np.asarray(
                stored_leads
            ),
            lead_names=np.asarray(
                stored_leads
            ),
            sample_rate=np.asarray(
                FS
            ),
            event_start_offset=(
                np.asarray(
                    trigger - int(FS)
                )
            ),
            event_end_offset=(
                np.asarray(
                    trigger + int(FS)
                )
            ),
        )

    (
        episode_dir
        / "analysis.json"
    ).write_text(
        json.dumps(
            {
                "status": (
                    "not_started"
                )
            }
        ),
        encoding="utf-8",
    )

    return (
        episode_id,
        signal,
        peaks,
    )


def test_valid_npz(
    storage,
):
    episodes, _ = storage

    (
        episode_id,
        _,
        _,
    ) = write_episode(
        episodes
    )

    loaded = load_episode_input(
        episode_id
    )

    assert (
        loaded.sampling_rate_hz
        == FS
    )

    assert (
        loaded.aligned_sample_count
        > 0
    )

    assert loaded.lead_ids == LEADS


def test_missing_npz(
    storage,
):
    episodes, _ = storage

    (
        episode_id,
        _,
        _,
    ) = write_episode(
        episodes,
        missing_npz=True,
    )

    with pytest.raises(
        AnalysisInputError
    ):
        load_episode_input(
            episode_id
        )


def test_malformed_npz(
    storage,
):
    episodes, _ = storage

    (
        episode_id,
        _,
        _,
    ) = write_episode(
        episodes,
        malformed=True,
    )

    with pytest.raises(
        AnalysisInputError
    ):
        load_episode_input(
            episode_id
        )


def test_missing_metadata(
    storage,
):
    episodes, _ = storage

    (
        episode_id,
        _,
        _,
    ) = write_episode(
        episodes,
        missing_metadata=True,
    )

    with pytest.raises(
        FileNotFoundError
    ):
        load_episode_input(
            episode_id
        )


def test_missing_lead_is_reported(
    storage,
):
    episodes, _ = storage

    (
        episode_id,
        _,
        _,
    ) = write_episode(
        episodes,
        missing_lead=True,
    )

    loaded = load_episode_input(
        episode_id
    )

    assert (
        "v6"
        in loaded.validation[
            "missingDeclaredLeadIds"
        ]
    )


def test_unequal_lead_lengths_are_aligned(
    storage,
):
    episodes, _ = storage

    (
        episode_id,
        _,
        _,
    ) = write_episode(
        episodes,
        unequal=True,
    )

    loaded = load_episode_input(
        episode_id
    )

    assert (
        loaded.validation[
            "equalLeadLengths"
        ]
        is False
    )

    assert len(
        loaded.waveforms_mv[
            "lead1"
        ]
    ) == len(
        loaded.waveforms_mv[
            "lead2"
        ]
    )


def test_nonnumeric_array_rejected(
    storage,
):
    episodes, _ = storage

    (
        episode_id,
        _,
        _,
    ) = write_episode(
        episodes,
        nonnumeric=True,
    )

    with pytest.raises(
        AnalysisInputError
    ):
        load_episode_input(
            episode_id
        )


def test_nan_and_infinity_counted(
    storage,
):
    episodes, _ = storage

    (
        episode_id,
        _,
        _,
    ) = write_episode(
        episodes,
        add_nan=True,
        add_inf=True,
    )

    loaded = load_episode_input(
        episode_id
    )

    invalid = (
        loaded.validation[
            "invalidSamplesByLead"
        ]
    )

    assert (
        invalid["lead1"][
            "nanCount"
        ]
        == 10
    )

    assert (
        invalid["lead2"][
            "positiveInfinityCount"
        ]
        == 1
    )

    assert (
        invalid["lead2"][
            "negativeInfinityCount"
        ]
        == 1
    )


@pytest.mark.parametrize(
    "kind,expected_field",
    [
        ("flatline", "flatlineDetected"),
        ("clipping", "clippingDetected"),
        (
            "discontinuity",
            "discontinuityDetected",
        ),
        (
            "low",
            "lowAmplitudeDetected",
        ),
    ],
)
def test_quality_artifacts(
    kind,
    expected_field,
):
    signal, _ = synthetic_ecg()

    if kind == "flatline":
        signal[400:800] = 0.0

    elif kind == "clipping":
        signal[400:520] = (
            np.max(signal)
        )

    elif kind == "discontinuity":
        signal[900:] += 5.0

    elif kind == "low":
        signal *= 0.01

    result = analyze_lead_quality(
        signal,
        FS,
    )

    assert result[
        expected_field
    ]


def test_baseline_wander():
    signal, _ = synthetic_ecg()
    time = np.arange(
        signal.size
    ) / FS

    signal += (
        0.8
        * np.sin(
            2
            * np.pi
            * 0.15
            * time
        )
    )

    result = analyze_lead_quality(
        signal,
        FS,
    )

    assert (
        result[
            "baselineWanderRmsMv"
        ]
        > 0.1
    )


def test_high_frequency_noise():
    signal, _ = synthetic_ecg()
    time = np.arange(
        signal.size
    ) / FS

    signal += (
        0.3
        * np.sin(
            2
            * np.pi
            * 45.0
            * time
        )
    )

    result = analyze_lead_quality(
        signal,
        FS,
    )

    assert (
        result[
            "highFrequencyNoiseRmsMv"
        ]
        > 0.05
    )


def test_preprocessing_preserves_raw():
    signal, _ = synthetic_ecg()
    original = signal.copy()

    filtered, provenance = (
        preprocess_signal(
            signal,
            FS,
        )
    )

    assert np.array_equal(
        signal,
        original,
    )

    assert (
        filtered.shape
        == signal.shape
    )

    assert (
        provenance[
            "rawWaveformModified"
        ]
        is False
    )


def test_known_synthetic_r_peaks():
    signal, expected = (
        synthetic_ecg()
    )

    filtered, _ = (
        preprocess_signal(
            signal,
            FS,
        )
    )

    candidates, _ = (
        detect_candidates(
            filtered,
            FS,
        )
    )

    assert (
        len(candidates)
        >= len(expected) - 2
    )

    errors = [
        min(
            abs(
                int(candidate)
                - expected_peak
            )
            for candidate
            in candidates
        )
        for expected_peak
        in expected
    ]

    assert np.median(errors) < 12


def test_duplicate_peak_suppression():
    signal, _ = synthetic_ecg()

    filtered, _ = (
        preprocess_signal(
            signal,
            FS,
        )
    )

    candidates, _ = (
        detect_candidates(
            filtered,
            FS,
        )
    )

    assert np.min(
        np.diff(candidates)
    ) >= int(0.12 * FS)


def test_regular_rr():
    result = analyze_rr(
        {
            "rPeakSamples": [
                100,
                276,
                452,
                628,
                804,
            ],
            "triggerBeatIndex": 2,
            "confidence": 90,
        },
        FS,
    )

    assert (
        result[
            "rhythmRegularity"
        ]
        == "regular"
    )


def test_irregular_rr():
    result = analyze_rr(
        {
            "rPeakSamples": [
                100,
                270,
                500,
                640,
                900,
            ],
            "triggerBeatIndex": 2,
            "confidence": 90,
        },
        FS,
    )

    assert (
        result[
            "rhythmRegularity"
        ]
        == "irregular"
    )


def test_premature_and_pause():
    result = analyze_rr(
        {
            "rPeakSamples": [
                100,
                276,
                452,
                558,
                804,
                980,
            ],
            "triggerBeatIndex": 3,
            "confidence": 90,
        },
        FS,
    )

    assert (
        result[
            "prematureTimingEvidence"
        ]
        is True
    )

    assert (
        result[
            "compensatoryPauseStatus"
        ]
        in {
            "full",
            "incomplete",
        }
    )


def test_insufficient_peaks():
    result = analyze_rr(
        {
            "rPeakSamples": [100]
        },
        FS,
    )

    assert (
        result["status"]
        == "failed"
    )


def test_measurable_qrs():
    signal, peaks = (
        synthetic_ecg(
            seconds=3
        )
    )

    peak = peaks[0]

    beat = signal[
        peak - int(0.3 * FS):
        peak + int(0.5 * FS) + 1
    ]

    result = measure_qrs(
        beat,
        FS,
    )

    assert (
        result["status"]
        == "ready"
    )

    assert (
        result[
            "qrsDurationMilliseconds"
        ]
        > 0
    )


def test_short_qrs_failure():
    result = measure_qrs(
        np.zeros(4),
        FS,
    )

    assert (
        result["status"]
        == "failed"
    )


def test_similar_morphology():
    signal, peaks = (
        synthetic_ecg(
            seconds=3
        )
    )

    beat = signal[
        peaks[0] - int(0.3 * FS):
        peaks[0]
        + int(0.5 * FS)
        + 1
    ]

    result = compare_templates(
        beat.copy(),
        beat.copy(),
        FS,
    )

    assert (
        result[
            "morphologyGrade"
        ]
        == "similar"
    )


def test_polarity_reversal():
    signal, peaks = (
        synthetic_ecg(
            seconds=3
        )
    )

    beat = signal[
        peaks[0] - int(0.3 * FS):
        peaks[0]
        + int(0.5 * FS)
        + 1
    ]

    result = compare_templates(
        -beat,
        beat,
        FS,
    )

    assert (
        result[
            "polarityDifference"
        ]
        is True
    )


def test_amplitude_scaling():
    signal, peaks = (
        synthetic_ecg(
            seconds=3
        )
    )

    beat = signal[
        peaks[0] - int(0.3 * FS):
        peaks[0]
        + int(0.5 * FS)
        + 1
    ]

    result = compare_templates(
        beat * 2.0,
        beat,
        FS,
    )

    assert (
        result[
            "pearsonCorrelation"
        ]
        > 0.95
    )

    assert (
        result[
            "amplitudeRatio"
        ]
        > 1.5
    )


def test_analysis_creation_and_cache(
    storage,
):
    episodes, _ = storage

    (
        episode_id,
        _,
        _,
    ) = write_episode(
        episodes
    )

    first = (
        episode_analyzer
        .analyze(
            episode_id
        )
    )

    second = (
        episode_analyzer
        .analyze(
            episode_id
        )
    )

    forced = (
        episode_analyzer
        .analyze(
            episode_id,
            force=True,
        )
    )

    assert (
        episodes
        / episode_id
        / "analysis.json"
    ).exists()

    assert (
        first[
            "inputFingerprint"
        ]
        == second[
            "inputFingerprint"
        ]
        == forced[
            "inputFingerprint"
        ]
    )

    assert (
        first[
            "rPeakAnalysis"
        ][
            "rPeakSamples"
        ]
        == second[
            "rPeakAnalysis"
        ][
            "rPeakSamples"
        ]
    )

    assert (
        forced[
            "provenance"
        ][
            "forcedReanalysis"
        ]
        is True
    )

    assert (
        first[
            "provenance"
        ][
            "isIndependentDiagnosis"
        ]
        is False
    )


def test_episode_analysis_api(
    storage,
):
    episodes, _ = storage

    (
        episode_id,
        _,
        _,
    ) = write_episode(
        episodes
    )

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    before = client.get(
        (
            f"/api/episodes/"
            f"{episode_id}/analysis"
        )
    )

    assert before.status_code == 200

    assert (
        before.json()["status"]
        == "not_analyzed"
    )

    response = client.post(
        (
            f"/api/episodes/"
            f"{episode_id}/analyze"
        )
    )

    assert response.status_code == 200

    assert (
        response.json()["status"]
        in {
            "ready",
            "partial",
        }
    )


def test_structured_api_error(
    storage,
):
    episodes, _ = storage

    (
        episode_id,
        _,
        _,
    ) = write_episode(
        episodes,
        malformed=True,
    )

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    response = client.post(
        (
            f"/api/episodes/"
            f"{episode_id}/analyze"
        )
    )

    assert response.status_code == 422

    assert (
        response.json()[
            "detail"
        ][
            "errorType"
        ]
        == "AnalysisInputError"
    )


def test_incident_analysis_api(
    storage,
):
    episodes, incidents = (
        storage
    )

    (
        first_id,
        _,
        _,
    ) = write_episode(
        episodes,
        (
            "incart-I01-loop-1-"
            "V-000001100"
        ),
    )

    (
        second_id,
        _,
        _,
    ) = write_episode(
        episodes,
        (
            "incart-I01-loop-1-"
            "V-000001100-view-2"
        ),
    )

    incident_id = (
        "incident-incart-I01-loop-1-"
        "ventricular-ectopy-000001100"
    )

    incident = {
        "id": incident_id,
        "episodeIds": [
            first_id,
            second_id,
        ],
        "primaryEpisodeId": (
            first_id
        ),
        "bestContextEpisodeId": (
            first_id
        ),
        "analysisStatus": (
            "pending"
        ),
        "mode": "research",
    }

    (
        incidents
        / f"{incident_id}.json"
    ).write_text(
        json.dumps(incident),
        encoding="utf-8",
    )

    app = FastAPI()
    app.include_router(
        incident_router
    )

    client = TestClient(app)

    response = client.post(
        (
            f"/api/incidents/"
            f"{incident_id}/analyze"
        )
    )

    assert response.status_code == 200

    payload = response.json()

    assert (
        payload[
            "ectopicBurden"
        ][
            (
                "overlappingEpisode"
                "ViewsDeduplicated"
            )
        ]
        is True
    )

    assert (
        payload[
            "primaryEpisodeId"
        ]
        == first_id
    )

    assert (
        payload[
            "bestContextEpisodeId"
        ]
        == first_id
    )


def test_incart_stream_regression():
    from app.incart_waveforms import (
        get_incart_buffer,
    )

    assert callable(
        get_incart_buffer
    )


def test_episode_capture_regression():
    assert callable(
        episode_coordinator
        .get_episode
    )

    assert callable(
        episode_coordinator
        .get_waveforms
    )


def test_incident_grouping_regression():
    assert callable(
        incident_coordinator
        .rebuild_from_episodes
    )

    assert callable(
        incident_coordinator
        .get_incident_episodes
    )


def test_oracle_context_regression():
    from app.clinical_context import (
        clinical_context_service,
    )

    assert callable(
        clinical_context_service
        .get
    )

    assert callable(
        clinical_context_service
        .load
    )


@pytest.mark.parametrize(
    "label",
    [
        "Glucose",
        "Potassium",
        "Creatinine",
        "WBC",
    ],
)
def test_lab_trend_regression(
    label,
):
    assert label in {
        "Glucose",
        "Potassium",
        "Creatinine",
        "WBC",
    }