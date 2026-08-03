from __future__ import annotations

import json
from typing import Any


SYSTEM_PROMPT = """You are the CARDINAL clinical-reasoning assistant evaluating one synthetic clinical episode.

Use only the supplied structured evidence. The ECG measurements are deterministic backend measurements. Do not claim to interpret raw waveform samples. Do not invent missing values, history, medications, laboratory results, timing, or diagnoses.

Return one JSON object with exactly these keys:
- episodeSummary
- rhythmInterpretation
- clinicalContext
- mostLikelyEtiology
- contributingFactors
- recommendedImmediateActions
- uncertaintyAndMissingData

The first four values must be strings. The last three values must be arrays of strings.

Prioritize urgent actions for unstable or immediately life-threatening evidence. State uncertainty when evidence is incomplete. Return JSON only."""


def build_messages(
    sanitized_episode: dict[str, Any],
) -> list[dict[str, str]]:
    episode_json = json.dumps(
        sanitized_episode,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    return [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": (
                "Produce the requested clinical "
                "evaluation output from this "
                "structured synthetic episode.\n\n"
                "EPISODE_DATA_JSON:\n"
                f"{episode_json}"
            ),
        },
    ]
