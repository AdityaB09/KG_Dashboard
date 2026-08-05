from __future__ import annotations

from typing import Any

from .model_clinical_evidence import sanitize_presentation_items
from .response_contract import validate_model_response_v6_0_3


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def adapt_v6_0_3_to_legacy_validator(payload: dict[str, Any]) -> dict[str, Any]:
    """Map model-owned fields after generation without another model call."""
    validate_model_response_v6_0_3(payload)
    summary = _clean(payload["episodeSummary"])
    etiology = _clean(payload["mostLikelyEtiologyAndClinicalContext"])
    factors = sanitize_presentation_items(payload["contributingFactors"])[:5]
    uncertainty = sanitize_presentation_items(payload["materialEtiologicUncertainty"])[:2]
    if not summary or not etiology or not factors:
        raise ValueError("Sanitized V6.0.3 output is missing required clinical content.")
    return {
        "episodeSummary": summary,
        "detectedEpisodeContext": summary,
        "mostLikelyEtiology": etiology,
        "contributingFactors": factors,
        "uncertaintyAndMissingData": uncertainty,
    }
