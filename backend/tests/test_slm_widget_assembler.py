from app.slm_widget.assembler import (
    _deterministic_metrics,
    _deterministic_narrative,
)
from app.slm_widget.validator import (
    safe_text,
    validated_contributors,
)


EVIDENCE = {
    "incident": {
        "display": "Ventricular Ectopy incident",
        "episodeCount": 2,
        "durationSeconds": 69.659,
        "severity": "warning",
    },
    "ecgEvidence": {
        "rhythm": {
            "medianHeartRateBpm": 94.25,
            "heartRateRangeBpm": [90.72, 97.78],
        },
        "qrs": {
            "medianTriggerQrsDurationMilliseconds": 93.182,
            "medianBaselineQrsDurationMilliseconds": 65.909,
        },
        "morphology": {
            "medianDifferenceScore": 0.2996,
        },
        "leadAgreement": {
            "medianScore": 72.14,
        },
        "deterministicConfidence": {
            "score": 83.41,
            "grade": "high",
        },
    },
    "referenceAnnotations": {
        "triggerCounts": {"V": 19},
        "isIndependentDiagnosis": False,
    },
}


def test_deterministic_widget_fields():
    narrative = _deterministic_narrative(EVIDENCE)

    assert "2 stored episode windows" in narrative["episodeNarrative"]
    assert "94.2 bpm" in narrative["arrhythmiaNarrative"]

    metrics = _deterministic_metrics(EVIDENCE)
    keys = {item["key"] for item in metrics}

    assert "episodeCount" in keys
    assert "referenceVCount" in keys
    assert "triggerQrsMilliseconds" in keys


def test_unsafe_model_claim_is_rejected():
    assert safe_text(
        "The episode was caused by potassium."
    ) is None

    assert safe_text(
        "The patient is stable."
    ) is None


def test_remote_contributor_is_confidence_capped():
    payload = {
        "possibleContributors": [
            {
                "title": "Remote electrolyte abnormality",
                "confidence": 0.92,
                "temporalFit": "historical_remote",
                "evidenceFor": [
                    "A historical result exists."
                ],
                "evidenceAgainst": [
                    "It was not episode-near."
                ],
            }
        ]
    }

    contributors = validated_contributors(
        payload,
        episode_near_medication_administration_count=0,
    )

    assert len(contributors) == 1
    assert contributors[0]["confidenceScore"] == 35.0
