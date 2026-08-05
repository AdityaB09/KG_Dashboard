from __future__ import annotations

from typing import Any

RESPONSE_CONTRACT_VERSION = "model-clinical-output-v6.0.3"
MODEL_RESPONSE_FIELDS = (
    "episodeSummary",
    "mostLikelyEtiologyAndClinicalContext",
    "contributingFactors",
    "materialEtiologicUncertainty",
)

MODEL_RESPONSE_SCHEMA_V6_0_3: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": list(MODEL_RESPONSE_FIELDS),
    "properties": {
        "episodeSummary": {"type": "string", "minLength": 1},
        "mostLikelyEtiologyAndClinicalContext": {"type": "string", "minLength": 1},
        "contributingFactors": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "items": {"type": "string", "minLength": 1},
        },
        "materialEtiologicUncertainty": {
            "type": "array",
            "minItems": 0,
            "maxItems": 2,
            "items": {"type": "string", "minLength": 1},
        },
    },
}


def validate_model_response_v6_0_3(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("V6.0.3 model output must be a JSON object.")
    actual = set(payload)
    expected = set(MODEL_RESPONSE_FIELDS)
    if actual != expected:
        raise ValueError(
            "V6.0.3 model output must contain exactly four fields; "
            f"missing={sorted(expected - actual)}; extra={sorted(actual - expected)}"
        )
    for field in MODEL_RESPONSE_FIELDS[:2]:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string.")
    factors = payload.get("contributingFactors")
    if not isinstance(factors, list) or not 1 <= len(factors) <= 5:
        raise ValueError("contributingFactors must contain 1 to 5 strings.")
    if not all(isinstance(item, str) and item.strip() for item in factors):
        raise ValueError("contributingFactors must contain non-empty strings.")
    uncertainty = payload.get("materialEtiologicUncertainty")
    if not isinstance(uncertainty, list) or len(uncertainty) > 2:
        raise ValueError("materialEtiologicUncertainty must contain 0 to 2 strings.")
    if not all(isinstance(item, str) and item.strip() for item in uncertainty):
        raise ValueError("materialEtiologicUncertainty must contain non-empty strings.")
    return payload
