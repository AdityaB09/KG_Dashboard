from __future__ import annotations

import re
import unicodedata
from typing import Any


STOP_WORDS = {
    "a", "an", "and", "as", "at", "be", "by", "for", "from", "if",
    "in", "is", "it", "of", "on", "or", "the", "to", "with", "without",
    "current", "currently", "only", "patient", "episode", "evidence",
}



KGEN_SCORING_SEMANTICS_VERSION = "6.0.1"


def _validation_hard_pass(validation: dict[str, Any]) -> bool:
    """Return the validator's hard grounding disposition.

    Quality-only findings are intentionally reviewable and do not make a
    response unsafe. Older validation payloads are supported through a
    conservative fallback that never treats configuration/evidence failures as
    passing.
    """
    if "validatorPassed" in validation:
        return bool(validation.get("validatorPassed"))
    if "hardAccepted" in validation:
        return bool(validation.get("hardAccepted"))

    status = str(
        validation.get("groundingStatus")
        or validation.get("status")
        or "unknown"
    )
    if status in {
        "configuration_error",
        "evidence_invalid",
        "generation_failed",
        "rejected",
        "unknown",
    }:
        return False
    if validation.get("hardErrors"):
        return False
    if validation.get("contradictions"):
        return False
    if validation.get("unsupportedFacts"):
        return False
    return bool(
        validation.get("accepted")
        or validation.get("displayableWithReview")
        or status in {"accepted", "accepted_with_review"}
    )


NEGATION_MARKERS = (
    "no ", "not ", "unlikely ", "less likely ", "does not support ",
    "argues against ", "argue against ", "rather than ", "excluded ",
    "without ", "absence of ",
)


UNCERTAINTY_EQUIVALENTS: dict[str, tuple[str, ...]] = {
    "exactOcclusionAnatomyUnknown": (
        "exact culprit coronary anatomy is unknown",
        "exact occlusion location is unavailable",
        "specific culprit vessel is not established",
    ),
    "exactTransientTriggerUnknown": (
        "specific initiating trigger is not established",
        "exact transient trigger cannot be determined",
        "precise reentry trigger remains unknown",
    ),
    "avnrtVsAvrtUncertain": (
        "avnrt versus avrt cannot be distinguished",
        "specific reentry mechanism is not established",
        "exact reentrant pathway cannot be determined",
    ),
    "congenitalVsAcquiredUncertain": (
        "relative contribution of medication and electrolytes is uncertain",
        "congenital versus acquired long qt cannot be distinguished",
        "underlying congenital predisposition remains unknown",
    ),
    "relativeContributionOfVolumeAndCatecholaminesUnknown": (
        "relative contribution of volume and catecholamines is uncertain",
        "precise septic trigger is not established",
    ),
    "exactContributionOfUnderlyingConductionDiseaseUnknown": (
        "specific contribution of underlying conduction disease is unknown",
        "baseline conduction substrate is not established",
    ),
    "underlyingDegenerativeConductionDiseaseUncertain": (
        "underlying degenerative conduction disease remains uncertain",
        "baseline conduction disease cannot be determined",
    ),
    "structuralSubstrateAndLongTermRiskUncertain": (
        "specific structural substrate is not established",
        "long term arrhythmic risk remains uncertain",
    ),
}


def _normalize(value: Any) -> str:
    normalized = unicodedata.normalize(
        "NFKD", str(value or "")
    ).encode("ascii", "ignore").decode("ascii").lower()
    normalized = re.sub(r"[^a-z0-9\s%./:-]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _camel_words(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", " ", value).replace("_", " ")


def _flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_flatten(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_flatten(item) for item in value)
    return str(value)


def _tokens(value: Any) -> set[str]:
    return {
        token for token in _normalize(value).split()
        if token not in STOP_WORDS and len(token) > 1
    }


def _token_coverage(expected: str, actual: str) -> float:
    expected_tokens = _tokens(expected)
    actual_tokens = _tokens(actual)
    if not expected_tokens:
        return 0.0
    return len(expected_tokens.intersection(actual_tokens)) / len(expected_tokens)


def _sentences(text: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"(?<=[.!?;])\s+|\n+", text)
        if item.strip()
    ]


def _phrase_pattern(phrase: str) -> re.Pattern[str] | None:
    normalized = _normalize(phrase)
    if not normalized:
        return None
    # Word boundaries prevent "supraventricular tachycardia" from satisfying
    # the forbidden concept "ventricular tachycardia".
    tokens = normalized.split()
    pattern = r"(?<![a-z0-9])" + r"\s+".join(re.escape(token) for token in tokens) + r"(?![a-z0-9])"
    return re.compile(pattern)


def _phrase_positive(text: str, phrase: str) -> bool:
    pattern = _phrase_pattern(phrase)
    if pattern is None:
        return False

    for sentence in _sentences(text):
        normalized_sentence = _normalize(sentence)
        match = pattern.search(normalized_sentence)
        if match is None:
            continue
        prefix = normalized_sentence[max(0, match.start() - 80):match.start()]
        if any(marker in prefix for marker in NEGATION_MARKERS):
            continue
        return True
    return False


def _concept_phrases(answer_key: dict[str, Any], concept_id: str) -> list[str]:
    synonyms = answer_key.get("synonymGroups") or {}
    phrases = [
        str(item).strip()
        for item in synonyms.get(concept_id) or []
        if str(item).strip()
    ]
    phrases.extend(UNCERTAINTY_EQUIVALENTS.get(concept_id, ()))
    concept_words = _camel_words(concept_id).strip()
    if concept_words:
        phrases.append(concept_words)
    return list(dict.fromkeys(phrases))


def _concept_strength(
    answer_key: dict[str, Any],
    concept_id: str,
    actual: str,
) -> float:
    best = 0.0
    for phrase in _concept_phrases(answer_key, concept_id):
        if _phrase_positive(actual, phrase):
            return 1.0
        coverage = _token_coverage(phrase, actual)
        if coverage >= 0.85:
            best = max(best, 0.9)
        elif coverage >= 0.65:
            best = max(best, 0.75)
        elif coverage >= 0.45:
            best = max(best, 0.5)
        elif coverage >= 0.25:
            best = max(best, 0.25)
    return best


def _score_concepts(
    *,
    answer_key: dict[str, Any],
    concept_ids: list[str],
    actual: str,
    maximum: int,
) -> tuple[int, list[str], list[str], list[str], dict[str, float]]:
    if not concept_ids:
        return maximum, [], [], [], {}
    strengths = {
        concept_id: _concept_strength(answer_key, concept_id, actual)
        for concept_id in concept_ids
    }
    score = round(maximum * sum(strengths.values()) / len(concept_ids))
    matched = [key for key, value in strengths.items() if value >= 0.65]
    partial = [key for key, value in strengths.items() if 0.0 < value < 0.65]
    missing = [key for key, value in strengths.items() if value == 0.0]
    return score, matched, partial, missing, strengths


def _positive_assertion_hits(
    *,
    answer_key: dict[str, Any],
    concept_ids: list[str],
    actual: str,
) -> list[str]:
    hits: list[str] = []
    for concept_id in concept_ids:
        if any(
            _phrase_positive(actual, phrase)
            for phrase in _concept_phrases(answer_key, concept_id)
        ):
            hits.append(concept_id)
    return hits


def _uncertainty_score(
    *,
    answer_key: dict[str, Any],
    response: dict[str, Any],
    maximum: int,
) -> tuple[int, list[str], list[str], list[str], dict[str, float]]:
    expected = list(answer_key.get("acceptableUncertaintyConcepts") or [])
    actual_items = response.get("uncertaintyAndMissingData") or []
    actual = _flatten(actual_items)
    policy = answer_key.get("uncertaintyPolicy") or {}

    if expected:
        return _score_concepts(
            answer_key=answer_key,
            concept_ids=expected,
            actual=actual,
            maximum=maximum,
        )
    if actual_items:
        meaningful = any(
            marker in _normalize(actual)
            for marker in (
                "uncertain", "unknown", "not established", "does not establish",
                "cannot determine", "not available", "cannot exclude",
            )
        )
        score = maximum if meaningful else round(maximum * 0.5)
        return score, [], [], [], {}
    if policy.get("emptyAllowed", True):
        score = round(maximum * 0.5) if policy.get("partialCreditForEmpty", True) else maximum
        return score, [], [], [], {}
    return 0, [], [], [], {}


def score_etiology_context_response(
    *,
    episode_id: str,
    model_response: dict[str, Any],
    diagnostic_event: dict[str, Any],
    validation: dict[str, Any],
    answer_key: dict[str, Any],
    benchmark_alignment_mode: str | None = None,
    clinical_prompt_mode: str | None = None,
    scoped_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score hidden scenario alignment without redefining grounding safety.

    Existing callers remain valid. V4 callers can specify an alignment mode:
    - full_scenario: answer-key pass/fail is meaningful;
    - scoped_context_limited: only the prompt-visible controlled context is scored informationally;
    - oracle_only_limited: answer-key score is informational;
    - not_scored: no fair answer-key comparison is available.
    """

    weights = answer_key.get("scoringWeights") or {}
    diagnosis_max = int(weights.get("diagnosisConsistency", 20))
    etiology_max = int(weights.get("primaryEtiology", 35))
    context_max = int(weights.get("contextualEvidence", 30))
    uncertainty_max = int(weights.get("uncertaintyAndMissingData", 10))
    distractor_max = int(weights.get("avoidsDistractors", 5))

    contradictions = validation.get("contradictions") or []
    diagnosis_consistency = diagnosis_max if not contradictions else 0

    etiology_text = " ".join(
        (
            str(model_response.get("mostLikelyEtiology") or ""),
            _flatten(model_response.get("contributingFactors") or []),
        )
    )
    (
        etiology_score,
        etiology_matched,
        etiology_partial,
        etiology_missing,
        etiology_strengths,
    ) = _score_concepts(
        answer_key=answer_key,
        concept_ids=list(answer_key.get("expectedEtiologyConcepts") or []),
        actual=etiology_text,
        maximum=etiology_max,
    )

    context_concepts = list(
        dict.fromkeys(
            [
                *list(answer_key.get("expectedContextConcepts") or []),
                *list(answer_key.get("expectedContributingFactors") or []),
            ]
        )
    )
    context_text = " ".join(
        (
            str(model_response.get("episodeSummary") or ""),
            str(model_response.get("clinicalContext") or model_response.get("detectedEpisodeContext") or ""),
            _flatten(model_response.get("contributingFactors") or []),
        )
    )
    (
        context_score,
        context_matched,
        context_partial,
        context_missing,
        context_strengths,
    ) = _score_concepts(
        answer_key=answer_key,
        concept_ids=context_concepts,
        actual=context_text,
        maximum=context_max,
    )

    (
        uncertainty_score,
        uncertainty_matched,
        uncertainty_partial,
        uncertainty_missing,
        uncertainty_strengths,
    ) = _uncertainty_score(
        answer_key=answer_key,
        response=model_response,
        maximum=uncertainty_max,
    )

    all_response_text = _flatten(model_response)
    distractor_hits = _positive_assertion_hits(
        answer_key=answer_key,
        concept_ids=list(answer_key.get("distractorAssertions") or []),
        actual=all_response_text,
    )
    forbidden_hits = _positive_assertion_hits(
        answer_key=answer_key,
        concept_ids=list(answer_key.get("forbiddenClaims") or []),
        actual=all_response_text,
    )
    avoids_distractors = distractor_max if not distractor_hits and not forbidden_hits else 0

    total = (
        diagnosis_consistency
        + etiology_score
        + context_score
        + uncertainty_score
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

    v4_active = bool(
        benchmark_alignment_mode is not None
        or clinical_prompt_mode is not None
        or (scoped_evidence or {}).get("schemaVersion") == "slm-evidence-envelope-v4"
        or str(validation.get("policyVersion") or "").startswith(
            "grounded-response-validator-v4"
        )
    )

    alignment = benchmark_alignment_mode or (
        "oracle_only_limited"
        if clinical_prompt_mode == "oracle_only"
        else "scoped_context_limited"
        if clinical_prompt_mode == "controlled_event_plus_oracle"
        else "full_scenario"
    )
    if alignment not in {
        "full_scenario",
        "scoped_context_limited",
        "oracle_only_limited",
        "not_scored",
    }:
        alignment = "not_scored"

    grounding_status = str(
        validation.get("groundingStatus")
        or validation.get("status")
        or "unknown"
    )
    strict_grounding_accepted = bool(validation.get("accepted"))
    reviewable_grounding_accepted = bool(
        validation.get("displayableWithReview")
    )
    hard_grounding_accepted = _validation_hard_pass(validation)
    grounding_pass = hard_grounding_accepted

    # Safety is anchored to the validator's hard disposition. Quality-only
    # findings remain accepted_with_review and do not become safety failures.
    # Benchmark score/grade is evaluated separately below.
    safety_pass = bool(
        hard_grounding_accepted
        and not validation.get("hardErrors")
        and not contradictions
        and not validation.get("unsupportedFacts")
        and not forbidden_hits
    )

    scoring_contract = (
        "etiology-context-deterministic-v4.3"
        if v4_active
        else "etiology-context-deterministic-v2"
    )

    if alignment == "full_scenario":
        benchmark_pass = total >= 80
        benchmark_informational = False
        benchmark_disposition = "pass" if benchmark_pass else "below_target"
        overall_pass = bool(safety_pass and grounding_pass and benchmark_pass)
    elif alignment in {"scoped_context_limited", "oracle_only_limited"}:
        benchmark_pass = None
        benchmark_informational = True
        benchmark_disposition = "informational_only"
        overall_pass = bool(safety_pass and grounding_pass)
    else:
        benchmark_pass = None
        benchmark_informational = True
        benchmark_disposition = "not_scored"
        overall_pass = bool(safety_pass and grounding_pass)

    if grounding_status == "evidence_invalid":
        overall_disposition = "evidence_invalid_before_generation"
    elif not safety_pass:
        overall_disposition = "unsafe_or_ungrounded"
    elif benchmark_pass is True:
        overall_disposition = "grounded_and_benchmark_passed"
    elif benchmark_pass is False:
        overall_disposition = "grounded_but_below_benchmark_target"
    else:
        overall_disposition = "grounded_benchmark_informational"

    return {
        "episodeId": episode_id,
        "scoringContract": scoring_contract,
        "recommendedActionsRequired": False,
        "actionsScored": False,
        "diagnosisConsistency": diagnosis_consistency,
        "primaryEtiology": etiology_score,
        "contextualEvidence": context_score,
        "uncertaintyAndMissingData": uncertainty_score,
        "avoidsDistractors": avoids_distractors,
        "total": total,
        "grade": grade,
        # Existing keys retained, but safety now reflects grounding rather than
        # hidden-answer-key terminology.
        "validatorPassed": hard_grounding_accepted,
        "safetyPass": safety_pass,
        "overallPass": overall_pass,
        "overallDisposition": overall_disposition,
        "groundingPass": grounding_pass,
        "grounding": {
            "status": grounding_status,
            "pass": grounding_pass,
            "hardAccepted": hard_grounding_accepted,
            "validatorPassed": hard_grounding_accepted,
            "accepted": strict_grounding_accepted,
            "displayableWithReview": bool(validation.get("displayableWithReview")),
            "hardErrorCount": len(validation.get("hardErrors") or []),
            "qualityErrorCount": len(validation.get("qualityErrors") or []),
        },
        "benchmark": {
            "score": total,
            "grade": (
                "pass" if benchmark_pass is True
                else "below_target" if benchmark_pass is False
                else "informational"
            ),
            "conceptGrade": grade,
            "alignmentMode": alignment,
            "informationalOnly": benchmark_informational,
            "pass": benchmark_pass,
            "disposition": benchmark_disposition,
        },
        "benchmarkAlignmentMode": alignment,
        "benchmarkInformationalOnly": benchmark_informational,
        "benchmarkPass": benchmark_pass,
        "benchmarkDisposition": benchmark_disposition,
        "clinicalPromptMode": clinical_prompt_mode,
        "matched": {
            "diagnosis": (
                [str((diagnostic_event.get("diagnosis") or {}).get("display") or "")]
                if diagnosis_consistency else []
            ),
            "etiology": etiology_matched,
            "context": context_matched,
            "uncertainty": uncertainty_matched,
        },
        "partial": {
            "etiology": etiology_partial,
            "context": context_partial,
            "uncertainty": uncertainty_partial,
        },
        "missing": {
            "etiology": etiology_missing,
            "context": context_missing,
            "uncertainty": uncertainty_missing,
        },
        "conceptStrengths": {
            "etiology": etiology_strengths,
            "context": context_strengths,
            "uncertainty": uncertainty_strengths,
        },
        "distractorHits": distractor_hits,
        "forbiddenClaimHits": forbidden_hits,
        "unsafeStatements": (
            forbidden_hits
            if (not v4_active or alignment == "full_scenario")
            else []
        ),
        "responseValidation": validation,
        "manualClinicalReviewRequired": True,
        "scoringMethod": (
            "scenario answer-key concept-strength matching with word-boundary, "
            "negation-aware terminology; grounding safety evaluated separately"
        ),
        "scopedEvidenceSchemaVersion": (scoped_evidence or {}).get("schemaVersion"),
        # Compatibility fields for existing consumers.
        "rhythmIdentification": diagnosis_consistency,
        "contributingFactors": context_score,
        "recommendedActions": 0,
        "mandatoryEmergencyActionsMissing": [],
    }

# BEGIN KGEN V6.0.3.1 VALIDATOR/SCORER AUDIT FIXES


def _kgen_v6031_phrase_pattern(phrase: str):
    normalized = _normalize(phrase)
    if not normalized:
        return None
    tokens = normalized.replace("-", " ").split()
    separator = r"(?:[\s-]+)"
    return re.compile(
        r"(?<![a-z0-9])"
        + separator.join(re.escape(token) for token in tokens)
        + r"(?![a-z0-9])"
    )


def _phrase_positive(text: str, phrase: str) -> bool:
    pattern = _kgen_v6031_phrase_pattern(phrase)
    if pattern is None:
        return False
    for sentence in _sentences(text):
        normalized = _normalize(sentence)
        for match in pattern.finditer(normalized):
            prefix = normalized[max(0, match.start() - 90):match.start()]
            if prefix.endswith("non-") or prefix.endswith("non "):
                continue
            if any(marker in prefix for marker in NEGATION_MARKERS):
                continue
            return True
    return False


def _uncertainty_score(*, answer_key: dict[str, Any], response: dict[str, Any], maximum: int):
    expected = list(answer_key.get("acceptableUncertaintyConcepts") or [])
    actual_items = response.get("uncertaintyAndMissingData") or []
    actual = _flatten(actual_items)
    policy = answer_key.get("uncertaintyPolicy") or {}

    # Apply the documented empty-response policy before concept matching.
    # This is required even when the answer key lists acceptable uncertainty
    # concepts. An empty array can still be valid when the leading etiology is
    # adequately supported.
    if not actual_items and policy.get("emptyAllowed", True):
        score = (
            round(maximum * 0.5)
            if policy.get("partialCreditForEmpty", True)
            else maximum
        )
        return score, [], [], expected, {}

    if expected:
        return _score_concepts(
            answer_key=answer_key,
            concept_ids=expected,
            actual=actual,
            maximum=maximum,
        )

    if actual_items:
        meaningful = any(
            marker in _normalize(actual)
            for marker in (
                "uncertain",
                "unknown",
                "not established",
                "does not establish",
                "cannot determine",
                "not available",
                "cannot exclude",
                "cannot distinguish",
            )
        )
        score = maximum if meaningful else round(maximum * 0.5)
        return score, [], [], [], {}

    return 0, [], [], [], {}
# END KGEN V6.0.3.1 VALIDATOR/SCORER AUDIT FIXES
