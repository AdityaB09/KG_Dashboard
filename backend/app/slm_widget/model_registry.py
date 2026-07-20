from __future__ import annotations

from typing import Any


MODEL_REGISTRY: dict[str, dict[str, Any]] = {
    "med_qwen2": {
        "alias": "med_qwen2",
        "model": "hf.co/mradermacher/Med-Qwen2-7B-GGUF:Q4_K_M",
        "role": "primary_narrative",
        "parameters": "7B",
        "quantization": "Q4_K_M",
        "recommendedContext": 16384,
        "allowedOutputFields": [
            "episodeNarrative",
            "arrhythmiaNarrative",
            "morphologyNarrative",
            "rootCauseNarrative",
            "importantLimitations",
        ],
        "mustNotControl": [
            "severity",
            "timestamps",
            "measurements",
            "currentStateCodes",
            "medicationExposure",
            "clinicalActions",
        ],
    },
    "jsl_medllama3_v2_q8": {
        "alias": "jsl_medllama3_v2_q8",
        "model": (
            "hf.co/mradermacher/"
            "JSL-MedLlama-3-8B-v2.0-GGUF:Q8_0"
        ),
        "role": "fallback_narrative",
        "parameters": "8B",
        "quantization": "Q8_0",
        "recommendedContext": 16384,
        "allowedOutputFields": [
            "episodeNarrative",
            "arrhythmiaNarrative",
            "morphologyNarrative",
            "importantLimitations",
        ],
        "mustNotControl": [
            "severity",
            "timestamps",
            "duration",
            "measurements",
            "currentStateCodes",
            "medicationExposure",
            "clinicalActions",
        ],
        "deploymentNote": "Review model licensing before commercial deployment.",
    },
    "huatuo7": {
        "alias": "huatuo7",
        "model": "hf.co/QuantFactory/HuatuoGPT-o1-7B-GGUF:Q5_K_M",
        "role": "contributor_hypotheses",
        "parameters": "7B",
        "quantization": "Q5_K_M",
        "recommendedContext": 16384,
        "allowedOutputFields": ["possibleContributors"],
        "maximumAcceptedContributors": 2,
        "mustNotControl": [
            "finalNarrative",
            "severity",
            "timestamps",
            "measurements",
            "medicationExposure",
            "clinicalActions",
        ],
    },
}


def model_sequence() -> list[dict[str, Any]]:
    return [
        MODEL_REGISTRY["med_qwen2"],
        MODEL_REGISTRY["jsl_medllama3_v2_q8"],
    ]


def contributor_model() -> dict[str, Any]:
    return MODEL_REGISTRY["huatuo7"]
