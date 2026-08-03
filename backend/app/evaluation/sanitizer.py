from __future__ import annotations

from copy import deepcopy
from typing import Any


class EvaluationSanitizationError(
    RuntimeError
):
    pass


def create_slm_payload(
    episode: dict[str, Any],
    evaluation_number: int,
) -> dict[str, Any]:
    payload = deepcopy(episode)

    ecg = payload.get("ecg")
    if isinstance(ecg, dict):
        ecg.pop("waveform", None)

    ppg = payload.get("ppg")
    if isinstance(ppg, dict):
        ppg.pop("waveform", None)

    neutral_id = (
        f"EVAL-{evaluation_number:03d}"
    )
    payload["episodeId"] = neutral_id
    payload["incidentId"] = (
        f"inc-{neutral_id}"
    )

    episode_block = payload.get("episode")
    if not isinstance(
        episode_block,
        dict,
    ):
        episode_block = {}
        payload["episode"] = episode_block

    # These fields expose the answer in IDs such as
    # VFIB-STEMI-001 and CHB-HYPERK-005.
    episode_block["type"] = (
        "captured_cardiac_episode"
    )
    episode_block["display"] = (
        "Captured cardiac episode"
    )

    payload.pop("file", None)
    validate_sanitized_payload(payload)
    return payload


def validate_sanitized_payload(
    payload: dict[str, Any],
) -> None:
    ecg = payload.get("ecg", {}) or {}
    ppg = payload.get("ppg", {}) or {}

    if "waveform" in ecg:
        raise EvaluationSanitizationError(
            "ECG waveform remains in SLM payload."
        )

    if "waveform" in ppg:
        raise EvaluationSanitizationError(
            "PPG waveform remains in SLM payload."
        )

    if not str(
        payload.get("episodeId", "")
    ).startswith("EVAL-"):
        raise EvaluationSanitizationError(
            "Episode ID was not neutralized."
        )

    forbidden_keys = {
        "answer_key",
        "answerKey",
        "mustIdentify",
        "mustRecommend",
        "distractors",
        "primaryEtiology",
    }

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in forbidden_keys:
                    raise EvaluationSanitizationError(
                        "Answer-key-like field "
                        f"detected: {key}"
                    )
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
