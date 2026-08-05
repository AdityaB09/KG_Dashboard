from __future__ import annotations

import pytest

from app.evaluation_injection.compatibility_adapter import adapt_v6_0_3_to_legacy_validator
from app.evaluation_injection.response_contract import validate_model_response_v6_0_3


def sample() -> dict:
    return {
        "episodeSummary": "An abrupt regular narrow-complex tachycardia occurred with stable perfusion.",
        "mostLikelyEtiologyAndClinicalContext": "A re-entrant supraventricular mechanism is most likely.",
        "contributingFactors": ["Prior similar paroxysmal episodes", "Caffeine-related autonomic stimulation"],
        "materialEtiologicUncertainty": ["The evidence cannot distinguish AVNRT from AVRT."],
    }


def test_exact_four_field_contract_and_legacy_mapping() -> None:
    payload = sample()
    assert validate_model_response_v6_0_3(payload) is payload
    legacy = adapt_v6_0_3_to_legacy_validator(payload)
    assert legacy["detectedEpisodeContext"] == payload["episodeSummary"]
    assert legacy["mostLikelyEtiology"] == payload["mostLikelyEtiologyAndClinicalContext"]
    assert legacy["uncertaintyAndMissingData"] == payload["materialEtiologicUncertainty"]


def test_additional_field_rejected() -> None:
    payload = sample()
    payload["extra"] = "not allowed"
    with pytest.raises(ValueError):
        validate_model_response_v6_0_3(payload)
