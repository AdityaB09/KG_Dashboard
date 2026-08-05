from __future__ import annotations

import sys
import types
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT))

import app

PACKAGE_NAME = "app.evaluation_injection"
if PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(BACKEND_ROOT / "app" / "evaluation_injection")]
    package.__package__ = PACKAGE_NAME
    sys.modules[PACKAGE_NAME] = package

from app.evaluation_injection import etiology_context_scorer as scorer
from app.evaluation_injection import response_validator as validator


def test_regular_does_not_match_irregular() -> None:
    assert not validator._positive_phrase("The rhythm was irregular.", ("regular",))
    assert not scorer._phrase_positive("The rhythm was irregular.", "regular")


def test_sustained_does_not_match_non_sustained() -> None:
    text = "The patient had non-sustained ventricular tachycardia."
    assert not validator._positive_phrase(
        text,
        ("sustained ventricular tachycardia",),
    )
    assert not scorer._phrase_positive(
        text,
        "sustained ventricular tachycardia",
    )


def test_safe_rounding_is_supported() -> None:
    assert validator._kgen_v6031_numeric_equivalent(54.0, 54.098)


def test_empty_uncertainty_gets_partial_credit_without_expected_concepts() -> None:
    score, *_ = scorer._uncertainty_score(
        answer_key={
            "acceptableUncertaintyConcepts": [],
            "uncertaintyPolicy": {
                "emptyAllowed": True,
                "partialCreditForEmpty": True,
            },
        },
        response={"uncertaintyAndMissingData": []},
        maximum=10,
    )
    assert score == 5


def test_empty_uncertainty_gets_partial_credit_with_expected_concepts() -> None:
    score, matched, partial, missing, strengths = scorer._uncertainty_score(
        answer_key={
            "acceptableUncertaintyConcepts": ["mechanismUnknown"],
            "uncertaintyPolicy": {
                "emptyAllowed": True,
                "partialCreditForEmpty": True,
            },
            "synonymGroups": {
                "mechanismUnknown": ["mechanism cannot be distinguished"],
            },
        },
        response={"uncertaintyAndMissingData": []},
        maximum=10,
    )
    assert score == 5
    assert matched == []
    assert partial == []
    assert missing == ["mechanismUnknown"]
    assert strengths == {}


def test_non_etiologic_technical_conflict_not_required() -> None:
    evidence = {
        "measurementConflicts": [
            {
                "id": "qrs",
                "material": True,
                "controlledValue": 88,
                "independentValue": 54.098,
            }
        ]
    }
    assert validator._v4_measurement_conflict_errors(
        "No conflict statement is needed.",
        evidence,
    ) == []


def test_etiologic_conflict_is_required() -> None:
    evidence = {
        "measurementConflicts": [
            {
                "id": "qrs",
                "etiologyMaterial": True,
                "controlledValue": 88,
                "independentValue": 54.098,
            }
        ]
    }
    assert validator._v4_measurement_conflict_errors(
        "No comparison was provided.",
        evidence,
    )
