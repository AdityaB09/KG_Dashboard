from __future__ import annotations

from app.phase7.prompt_builder import (
    validate_evidence,
)


def test_safe_waveform_metadata_is_allowed():
    evidence = {
        "provenance": {
            "waveformSource": (
                "PhysioNet INCART"
            ),
        },
        "safety": {
            "isIndependentDiagnosis": (
                False
            ),
            "rawWaveformsIncluded": (
                False
            ),
        },
    }

    result = validate_evidence(
        evidence
    )

    assert result["status"] == "ready"
    assert result[
        "rawWaveformKeys"
    ] == []


def test_actual_waveform_array_key_is_rejected():
    evidence = {
        "waveforms": [
            0.1,
            0.2,
            0.3,
        ],
        "safety": {
            "isIndependentDiagnosis": (
                False
            ),
            "rawWaveformsIncluded": (
                False
            ),
        },
    }

    result = validate_evidence(
        evidence
    )

    assert result["status"] == "failed"
    assert "$.waveforms" in result[
        "rawWaveformKeys"
    ]


def test_raw_waveforms_included_true_is_rejected():
    evidence = {
        "provenance": {
            "waveformSource": (
                "PhysioNet INCART"
            ),
        },
        "safety": {
            "isIndependentDiagnosis": (
                False
            ),
            "rawWaveformsIncluded": (
                True
            ),
        },
    }

    result = validate_evidence(
        evidence
    )

    assert result["status"] == "failed"
