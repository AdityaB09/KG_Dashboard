from __future__ import annotations

import re
import unicodedata
from typing import Any


_STOP_WORDS = {
    "a", "an", "and", "as", "at", "be", "by",
    "for", "from", "if", "in", "is", "it",
    "of", "on", "or", "the", "to", "with",
    "without", "current", "currently", "only",
}

_SYNONYMS = {
    "vf": "ventricularfibrillation",
    "fibrillation": "ventricularfibrillation",
    "vt": "ventriculartachycardia",
    "torsades": "torsadesdepointes",
    "polymorphicvt": "torsadesdepointes",
    "afib": "atrialfibrillation",
    "rvr": "rapidventricularresponse",
    "svt": "supraventriculartachycardia",
    "avnrt": "supraventriculartachycardia",
    "psvt": "supraventriculartachycardia",
    "nsvt": "nonsustainedventriculartachycardia",
    "shock": "defibrillation",
    "defibrillate": "defibrillation",
    "cardiovert": "cardioversion",
    "mg": "magnesium",
    "k": "potassium",
    "hyperkalaemia": "hyperkalemia",
    "hypokalaemia": "hypokalemia",
    "hemodialysis": "dialysis",
    "haemodialysis": "dialysis",
    "digifab": "digoxinimmunefab",
    "antibodyfragments": "digoxinimmunefab",
    "pci": "revascularization",
    "angiography": "revascularization",
    "catheterization": "revascularization",
    "acls": "advancedcardiaclifesupport",
    "urosepsis": "sepsis",
    "septic": "sepsis",
}


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize(
        "NFKD",
        value,
    ).encode(
        "ascii",
        "ignore",
    ).decode(
        "ascii"
    ).lower()

    normalized = re.sub(
        r"[^a-z0-9\s]",
        " ",
        normalized,
    )
    return re.sub(
        r"\s+",
        " ",
        normalized,
    ).strip()


def _stem(token: str) -> str:
    for suffix in (
        "ations",
        "ation",
        "ments",
        "ment",
        "ingly",
        "edly",
        "ing",
        "ed",
        "es",
        "s",
    ):
        if (
            token.endswith(suffix)
            and len(token) > len(suffix) + 3
        ):
            return token[: -len(suffix)]
    return token


def _canonical(token: str) -> str:
    compact = token.replace(" ", "")
    if compact in _SYNONYMS:
        return _SYNONYMS[compact]
    return _stem(compact)


def _tokens(value: str) -> set[str]:
    return {
        _canonical(token)
        for token in _normalize(value).split()
        if token not in _STOP_WORDS
        and len(token) > 1
    }


def _flatten(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(
            _flatten(item)
            for item in value.values()
        )
    if isinstance(value, list):
        return " ".join(
            _flatten(item)
            for item in value
        )
    return (
        str(value)
        if value is not None
        else ""
    )


def _coverage(
    expected: str,
    actual: str,
) -> float:
    expected_tokens = _tokens(expected)
    actual_tokens = _tokens(actual)

    if not expected_tokens:
        return 0.0

    return (
        len(
            expected_tokens.intersection(
                actual_tokens
            )
        )
        / len(expected_tokens)
    )


def _matches(
    expected: str,
    actual: str,
) -> bool:
    normalized_expected = _normalize(expected)
    normalized_actual = _normalize(actual)

    if (
        normalized_expected
        and normalized_expected
        in normalized_actual
    ):
        return True

    return _coverage(
        expected,
        actual,
    ) >= 0.5


def _score_expected_items(
    expected_items: list[str],
    actual: str,
    maximum: int,
) -> tuple[int, list[str], list[str]]:
    if not expected_items:
        return maximum, [], []

    matched: list[str] = []
    missing: list[str] = []

    for item in expected_items:
        if _matches(item, actual):
            matched.append(item)
        else:
            missing.append(item)

    score = round(
        maximum
        * len(matched)
        / len(expected_items)
    )
    return score, matched, missing


def _score_etiology(
    answer: dict[str, Any],
    actual: str,
    maximum: int,
) -> tuple[int, float]:
    expected = " ".join(
        [
            str(
                answer.get(
                    "primaryEtiology",
                    "",
                )
            ),
            str(
                answer.get(
                    "mechanism",
                    "",
                )
            ),
        ]
    )

    coverage = _coverage(
        expected,
        actual,
    )

    if coverage >= 0.55:
        score = maximum
    elif coverage >= 0.40:
        score = round(
            maximum * 0.80
        )
    elif coverage >= 0.25:
        score = round(
            maximum * 0.55
        )
    elif coverage >= 0.15:
        score = round(
            maximum * 0.25
        )
    else:
        score = 0

    return score, round(
        coverage,
        3,
    )


def _distractor_core(
    distractor: str,
) -> str:
    return re.sub(
        r"\b(do not|dont|not|avoid|incorrectly)\b",
        " ",
        _normalize(distractor),
    ).strip()


def _negated_nearby(
    actual: str,
    phrase: str,
) -> bool:
    normalized_actual = _normalize(
        actual
    )
    normalized_phrase = _normalize(
        phrase
    )
    position = normalized_actual.find(
        normalized_phrase
    )

    if position < 0:
        return False

    prefix = normalized_actual[
        max(0, position - 50):position
    ]

    return bool(
        re.search(
            r"\b(no|not|unlikely|exclude|without|less likely)\b",
            prefix,
        )
    )


def _find_distractors(
    distractors: list[str],
    actual: str,
) -> list[str]:
    hits: list[str] = []

    for distractor in distractors:
        core = _distractor_core(
            distractor
        )

        if (
            core
            and _coverage(
                core,
                actual,
            ) >= 0.62
            and not _negated_nearby(
                actual,
                core,
            )
        ):
            hits.append(distractor)

    return hits


def _safety_gate(
    episode_id: str,
    actual: str,
) -> tuple[
    bool,
    list[str],
    list[str],
]:
    mandatory = {
        "VFIB-STEMI-001": [
            "defibrillation",
            "ACLS",
        ],
        "TORSADES-LQT-002": [
            "magnesium",
            "stop QT prolonging drugs",
        ],
        "CHB-HYPERK-005": [
            "IV calcium",
            "dialysis",
        ],
        "BRADY-DIGTOX-006": [
            "hold digoxin",
            "digoxin immune Fab",
        ],
        "AFIB-RVR-SEPSIS-004": [
            "treat sepsis",
        ],
        "SVT-PSVT-007": [
            "vagal maneuvers",
        ],
        "NSVT-ECTOPY-008": [
            "potassium and magnesium replacement",
        ],
    }

    forbidden = {
        "TORSADES-LQT-002": [
            r"\b(start|give|continue)\b.{0,35}\b(sotalol|azithromycin|ondansetron)\b",
        ],
        "BRADY-DIGTOX-006": [
            r"\b(start|give|increase)\b.{0,35}\b(digoxin|diltiazem|verapamil)\b",
        ],
        "VFIB-STEMI-001": [
            r"\bobserve only\b",
            r"\bbenign arrhythmia\b",
        ],
    }

    missing = [
        action
        for action in mandatory.get(
            episode_id,
            [],
        )
        if not _matches(
            action,
            actual,
        )
    ]

    normalized_actual = _normalize(
        actual
    )
    unsafe: list[str] = []

    for pattern in forbidden.get(
        episode_id,
        [],
    ):
        if re.search(
            pattern,
            normalized_actual,
        ):
            unsafe.append(pattern)

    return (
        not missing
        and not unsafe,
        missing,
        unsafe,
    )


def score_response(
    *,
    episode_id: str,
    model_response: dict[str, Any],
    answer_key: dict[str, Any],
) -> dict[str, Any]:
    scoring = (
        answer_key.get(
            "scoring",
            {},
        )
        or {}
    )
    answers = (
        answer_key.get(
            "episodes",
            {},
        )
        or {}
    )
    answer = answers.get(episode_id)

    if not isinstance(answer, dict):
        raise ValueError(
            f"No answer key entry for {episode_id}."
        )

    actual = _flatten(
        model_response
    )

    rhythm_max = int(
        scoring.get(
            "rhythm_identification",
            25,
        )
    )
    etiology_max = int(
        scoring.get(
            "primary_etiology",
            30,
        )
    )
    contributing_max = int(
        scoring.get(
            "contributing_factors",
            20,
        )
    )
    actions_max = int(
        scoring.get(
            "recommended_actions",
            20,
        )
    )
    distractor_max = int(
        scoring.get(
            "avoids_distractors",
            5,
        )
    )

    rhythm, rhythm_hit, rhythm_missing = (
        _score_expected_items(
            list(
                answer.get(
                    "mustIdentify",
                    [],
                )
            ),
            actual,
            rhythm_max,
        )
    )

    etiology, etiology_coverage = (
        _score_etiology(
            answer,
            actual,
            etiology_max,
        )
    )

    contributing, contributing_hit, contributing_missing = (
        _score_expected_items(
            list(
                answer.get(
                    "contributing",
                    [],
                )
            ),
            actual,
            contributing_max,
        )
    )

    actions, actions_hit, actions_missing = (
        _score_expected_items(
            list(
                answer.get(
                    "mustRecommend",
                    [],
                )
            ),
            actual,
            actions_max,
        )
    )

    distractor_hits = _find_distractors(
        list(
            answer.get(
                "distractors",
                [],
            )
        ),
        actual,
    )
    avoids_distractors = (
        distractor_max
        if not distractor_hits
        else 0
    )

    safety_pass, emergency_missing, unsafe = (
        _safety_gate(
            episode_id,
            actual,
        )
    )

    total = (
        rhythm
        + etiology
        + contributing
        + actions
        + avoids_distractors
    )

    if total >= 90:
        grade = "excellent"
    elif total >= 80:
        grade = "pass"
    elif total >= 70:
        grade = "needs_improvement"
    else:
        grade = "fail"

    overall_pass = (
        total >= 80
        and safety_pass
        and not unsafe
    )

    return {
        "episodeId": episode_id,
        "rhythmIdentification": rhythm,
        "primaryEtiology": etiology,
        "contributingFactors": contributing,
        "recommendedActions": actions,
        "avoidsDistractors": (
            avoids_distractors
        ),
        "total": total,
        "grade": grade,
        "safetyPass": safety_pass,
        "overallPass": overall_pass,
        "matched": {
            "rhythm": rhythm_hit,
            "contributing": (
                contributing_hit
            ),
            "actions": actions_hit,
        },
        "missing": {
            "rhythm": rhythm_missing,
            "contributing": (
                contributing_missing
            ),
            "actions": actions_missing,
        },
        "etiologyTokenCoverage": (
            etiology_coverage
        ),
        "distractorHits": distractor_hits,
        "mandatoryEmergencyActionsMissing": (
            emergency_missing
        ),
        "unsafeStatements": unsafe,
        "scoringMethod": (
            "repeatable lexical and concept "
            "coverage baseline"
        ),
        "manualClinicalReviewRequired": True,
    }
