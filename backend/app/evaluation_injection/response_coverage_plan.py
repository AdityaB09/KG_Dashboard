from __future__ import annotations

import os
import re
import unicodedata
from copy import deepcopy
from typing import Any


PLAN_SCHEMA = "response-coverage-plan-v4.4"
ASSESSMENT_SCHEMA = "response-coverage-assessment-v4.4"
SCORE_SCHEMA = "evidence-adjusted-grounding-score-v4.4"


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _normalize(value: Any) -> str:
    text = unicodedata.normalize(
        "NFKD",
        str(value or ""),
    ).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9\s%./:+-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


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


def _response_text(response: dict[str, Any]) -> str:
    return _normalize(
        " ".join(
            (
                str(response.get("episodeSummary") or ""),
                str(
                    response.get("detectedEpisodeContext")
                    or response.get("clinicalContext")
                    or ""
                ),
                str(response.get("mostLikelyEtiology") or ""),
                _flatten(response.get("contributingFactors") or []),
                _flatten(response.get("uncertaintyAndMissingData") or []),
            )
        )
    )


def _terms_from_text(value: Any, *, limit: int = 10) -> list[str]:
    stop = {
        "the", "and", "with", "from", "that", "this", "were", "was",
        "not", "supplied", "evidence", "available", "controlled",
        "event", "oracle", "patient", "context",
    }
    tokens = [
        token
        for token in _normalize(value).split()
        if len(token) > 2 and token not in stop
    ]
    return list(dict.fromkeys(tokens))[:limit]


def _matches_requirement(text: str, requirement: dict[str, Any]) -> bool:
    terms = [
        _normalize(item)
        for item in requirement.get("matchTerms") or []
        if _normalize(item)
    ]
    groups = requirement.get("matchAllGroups") or []

    if groups:
        for group in groups:
            normalized_group = [
                _normalize(item)
                for item in group
                if _normalize(item)
            ]
            if normalized_group and not any(term in text for term in normalized_group):
                return False
        return True

    return bool(terms and any(term in text for term in terms))


def _append_candidate(
    items: list[dict[str, Any]],
    *,
    identifier: str,
    instruction: str,
    source_paths: list[str],
    match_terms: list[str],
    category: str,
) -> None:
    if any(item.get("id") == identifier for item in items):
        return

    items.append(
        {
            "id": identifier,
            "category": category,
            "instruction": instruction,
            "sourcePaths": source_paths,
            "matchTerms": list(
                dict.fromkeys(term for term in match_terms if str(term).strip())
            ),
        }
    )


def _collect_limitations(value: Any) -> list[str]:
    output: list[str] = []

    if isinstance(value, dict):
        for key, item in value.items():
            if (
                key.lower() in {"limitations", "warnings", "missing", "excludedEvidence"}
                and isinstance(item, list)
            ):
                output.extend(
                    str(entry).strip()
                    for entry in item
                    if str(entry).strip()
                )
            else:
                output.extend(_collect_limitations(item))
    elif isinstance(value, list):
        for item in value:
            output.extend(_collect_limitations(item))

    return list(dict.fromkeys(output))


def _oracle_has_remote_or_future_data(oracle: dict[str, Any]) -> bool:
    items = [
        *list(oracle.get("labTrends") or []),
        *list(oracle.get("vitalTrends") or []),
    ]

    for item in items:
        if not isinstance(item, dict):
            continue

        bucket = str(item.get("temporalBucket") or "").strip().lower()
        relation = str(
            item.get("relation")
            or item.get("latestRelation")
            or ""
        ).strip().lower()

        if (
            bucket not in {"episode_near", "near_event", "current"}
            or relation == "after_anchor"
        ):
            return True

    return False


def _uncertainty_candidates(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    for conflict in evidence.get("measurementConflicts") or []:
        if isinstance(conflict, dict) and conflict.get("material"):
            identifier = str(conflict.get("id") or "measurement_conflict")
            instruction = str(
                conflict.get("requiredAcknowledgement")
                or "Acknowledge the supplied measurement conflict."
            )
            _append_candidate(
                candidates,
                identifier=identifier,
                instruction=instruction,
                source_paths=["measurementConflicts"],
                match_terms=[
                    "difference", "differs", "discrepancy", "conflict",
                    "controlled", "independent",
                ],
                category="measurement_conflict",
            )

    for item in evidence.get("missingEvidence") or []:
        if not isinstance(item, dict):
            continue

        identifier = str(
            item.get("id")
            or item.get("source")
            or "missing_evidence"
        )
        reason = str(
            item.get("reason")
            or "Evidence was not supplied."
        ).strip()

        _append_candidate(
            candidates,
            identifier=f"missing_{identifier}",
            instruction=reason,
            source_paths=[str(item.get("source") or "missingEvidence")],
            match_terms=[
                *_terms_from_text(identifier),
                *_terms_from_text(reason),
                "not supplied",
                "not available",
                "not returned",
            ],
            category="missing_evidence",
        )

    controlled = evidence.get("controlledEventContext") or {}
    electrolytes = controlled.get("electrolytes") or {}

    supplied: list[str] = []
    unavailable: list[str] = []

    for name in ("potassium", "magnesium", "calcium", "sodium"):
        item = electrolytes.get(name) or {}
        if item.get("available") is True:
            supplied.append(name)
        else:
            unavailable.append(name)

    if supplied and unavailable:
        _append_candidate(
            candidates,
            identifier="partial_electrolyte_panel",
            instruction=(
                "State which electrolytes were supplied and which were not supplied."
            ),
            source_paths=["controlledEventContext.electrolytes"],
            match_terms=[
                *supplied,
                *unavailable,
                "not supplied",
                "unavailable",
            ],
            category="laboratory_coverage",
        )

    oracle = evidence.get("oracleContext") or {}

    if _oracle_has_remote_or_future_data(oracle):
        _append_candidate(
            candidates,
            identifier="oracle_temporal_limit",
            instruction=(
                "State that available Oracle observations are historical, remote, "
                "or after the event rather than episode-time physiology."
            ),
            source_paths=[
                "oracleContext.labTrends",
                "oracleContext.vitalTrends",
            ],
            match_terms=[
                "historical",
                "remote",
                "after event",
                "not episode time",
                "not episode-time",
            ],
            category="oracle_timing",
        )

    availability = (
        oracle.get("resourceAvailability")
        or oracle.get("dataQuality")
        or {}
    )
    condition_count = availability.get("conditionCount")

    if condition_count in (0, None):
        _append_candidate(
            candidates,
            identifier="oracle_conditions_unavailable",
            instruction=(
                "State that Oracle conditions were not returned; "
                "do not infer disease absence."
            ),
            source_paths=["oracleContext.resourceAvailability"],
            match_terms=[
                "conditions",
                "not returned",
                "not available",
                "cannot establish",
            ],
            category="condition_availability",
        )

    limitations = _collect_limitations(
        evidence.get("deterministicAnalysis") or {}
    )

    if limitations:
        combined = " ".join(limitations[:4])
        _append_candidate(
            candidates,
            identifier="phase6_limitations",
            instruction=(
                "State the most clinically relevant Phase 6 measurement limitation."
            ),
            source_paths=["deterministicAnalysis"],
            match_terms=[
                *_terms_from_text(combined, limit=12),
                "confidence",
                "limitation",
                "partial",
            ],
            category="phase6_limitations",
        )

    return candidates


def build_response_coverage_plan(evidence: dict[str, Any]) -> dict[str, Any]:
    requirements = [
        deepcopy(item)
        for item in evidence.get("coverageRequirements") or []
        if isinstance(item, dict)
    ]

    required = [
        item
        for item in requirements
        if item.get("requiredInResponse") is True
    ]
    optional = [
        item
        for item in requirements
        if item.get("requiredInResponse") is not True
    ]

    preferred_optional_ids = {
        "etiologySupport",
        "oracleClinicalContext",
        "electrolyteInterpretation",
        "infectionContext",
        "renalContext",
        "qtContext",
        "medicationToxicityContext",
    }

    preferred_optional = [
        item
        for item in optional
        if item.get("id") in preferred_optional_ids
    ]

    uncertainty = _uncertainty_candidates(evidence)

    configured_minimum = _int_env(
        "SLM_MIN_UNCERTAINTY_ITEMS",
        3,
        0,
        6,
    )
    minimum_uncertainty = min(configured_minimum, len(uncertainty))

    prompt_checklist = [
        {
            "id": item.get("id"),
            "instruction": item.get("instruction"),
            "required": True,
        }
        for item in required
    ]
    prompt_checklist.extend(
        {
            "id": item.get("id"),
            "instruction": item.get("instruction"),
            "required": False,
        }
        for item in preferred_optional[:5]
    )

    return {
        "schemaVersion": PLAN_SCHEMA,
        "requiredCoverage": required,
        "preferredOptionalCoverage": preferred_optional[:5],
        "promptChecklist": prompt_checklist,
        "uncertaintyCandidates": uncertainty,
        "minimumUncertaintyItems": minimum_uncertainty,
        "retryEnabled": _flag("SLM_COVERAGE_RETRY_ENABLED", True),
        "policy": {
            "hiddenAnswerKeyUsed": False,
            "onlyPromptVisibleEvidenceUsed": True,
            "maximumModelAttempts": 2,
        },
    }


def assess_response_coverage(
    response: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    plan = (
        evidence.get("responseCoveragePlan")
        or build_response_coverage_plan(evidence)
    )
    text = _response_text(response)

    requirement_results: dict[str, bool] = {}

    for item in [
        *list(plan.get("requiredCoverage") or []),
        *list(plan.get("preferredOptionalCoverage") or []),
    ]:
        identifier = str(item.get("id") or "")
        if not identifier:
            continue
        requirement_results[identifier] = _matches_requirement(text, item)

    required_ids = [
        str(item.get("id"))
        for item in plan.get("requiredCoverage") or []
        if item.get("id")
    ]

    missing_required = [
        identifier
        for identifier in required_ids
        if not requirement_results.get(identifier, False)
    ]

    uncertainty_items = [
        str(item).strip()
        for item in response.get("uncertaintyAndMissingData") or []
        if str(item).strip()
    ]
    unique_uncertainty = list(
        dict.fromkeys(
            _normalize(item)
            for item in uncertainty_items
            if _normalize(item)
        )
    )
    uncertainty_text = _normalize(" ".join(uncertainty_items))

    candidate_results: dict[str, bool] = {}
    for candidate in plan.get("uncertaintyCandidates") or []:
        identifier = str(candidate.get("id") or "")
        terms = [
            _normalize(term)
            for term in candidate.get("matchTerms") or []
            if _normalize(term)
        ]
        candidate_results[identifier] = bool(
            terms and any(term in uncertainty_text for term in terms)
        )

    minimum_uncertainty = int(plan.get("minimumUncertaintyItems") or 0)
    uncertainty_count_ok = len(unique_uncertainty) >= minimum_uncertainty
    candidate_match_count = sum(
        1 for value in candidate_results.values() if value
    )
    candidate_match_ok = bool(
        minimum_uncertainty == 0
        or not candidate_results
        or candidate_match_count >= 1
    )
    uncertainty_ok = bool(uncertainty_count_ok and candidate_match_ok)

    errors: list[str] = []
    correction_evidence: list[dict[str, Any]] = []

    if missing_required:
        errors.append(
            "Supported evidence coverage is incomplete: "
            + ", ".join(missing_required)
        )
        for item in plan.get("requiredCoverage") or []:
            if item.get("id") in missing_required:
                correction_evidence.append(
                    {
                        "id": item.get("id"),
                        "instruction": item.get("instruction"),
                        "evidencePaths": item.get("evidencePaths") or [],
                    }
                )

    if not uncertainty_ok:
        errors.append(
            "Uncertainty coverage is incomplete. Return at least "
            f"{minimum_uncertainty} distinct uncertainty or missing-data "
            "items grounded in the supplied evidence."
        )
        correction_evidence.extend(
            {
                "id": item.get("id"),
                "instruction": item.get("instruction"),
                "sourcePaths": item.get("sourcePaths") or [],
            }
            for item in (plan.get("uncertaintyCandidates") or [])[:4]
        )

    return {
        "schemaVersion": ASSESSMENT_SCHEMA,
        "requirementCoverage": requirement_results,
        "requiredCoverageIds": required_ids,
        "missingRequiredCoverage": missing_required,
        "uncertaintyItemCount": len(unique_uncertainty),
        "minimumUncertaintyItems": minimum_uncertainty,
        "uncertaintyCandidateCoverage": candidate_results,
        "uncertaintyCandidateMatchCount": candidate_match_count,
        "uncertaintyCoveragePassed": uncertainty_ok,
        "retryNeeded": bool(missing_required or not uncertainty_ok),
        "errors": errors,
        "correctionEvidence": correction_evidence,
    }


def apply_coverage_assessment(
    validation: dict[str, Any],
    assessment: dict[str, Any],
    *,
    allow_retry: bool,
) -> dict[str, Any]:
    updated = deepcopy(validation)
    updated["responseCoveragePlanAssessment"] = assessment

    if not assessment.get("retryNeeded"):
        return updated

    # Never weaken or replace an existing deterministic hard rejection.
    if updated.get("hardErrors"):
        return updated

    coverage_errors = list(assessment.get("errors") or [])
    existing_errors = list(updated.get("errors") or [])
    existing_quality = list(updated.get("qualityErrors") or [])
    correction = list(updated.get("correctionEvidence") or [])

    updated["errors"] = list(
        dict.fromkeys([*existing_errors, *coverage_errors])
    )
    updated["qualityErrors"] = list(
        dict.fromkeys([*existing_quality, *coverage_errors])
    )
    updated["correctionEvidence"] = [
        *correction,
        *list(assessment.get("correctionEvidence") or []),
    ]

    if allow_retry:
        updated.update(
            {
                "status": "coverage_repair_required",
                "groundingStatus": "coverage_repair_required",
                "accepted": False,
                "displayableWithReview": False,
                "retryable": True,
                "coverageRepairRequired": True,
            }
        )
    else:
        updated.update(
            {
                "status": "accepted_with_review",
                "groundingStatus": "accepted_with_review",
                "accepted": False,
                "hardAccepted": True,
                "displayableWithReview": True,
                "retryable": False,
                "coverageRepairRequired": False,
            }
        )

    return updated


def calculate_evidence_adjusted_score(
    *,
    response: dict[str, Any],
    validation: dict[str, Any],
    evidence: dict[str, Any],
    assessment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    assessment = (
        assessment
        or assess_response_coverage(response, evidence)
    )
    coverage = (
        validation.get("evidenceCoverage")
        or assessment.get("requirementCoverage")
        or {}
    )

    displayable = bool(
        validation.get("accepted")
        or validation.get("displayableWithReview")
    )
    contradictions = list(validation.get("contradictions") or [])
    unsupported = list(validation.get("unsupportedFacts") or [])
    forbidden = list(validation.get("forbiddenClaimHits") or [])

    diagnosis_score = (
        20
        if displayable
        and not contradictions
        and coverage.get("rhythmEvidence", True)
        else 0
    )

    etiology_text = str(response.get("mostLikelyEtiology") or "").strip()
    etiology_covered = bool(
        coverage.get("etiologySupport")
        or etiology_text
    )
    etiology_score = (
        35
        if displayable and etiology_covered and not unsupported
        else 18
        if etiology_text
        else 0
    )

    required_ids = [
        item
        for item in assessment.get("requiredCoverageIds") or []
        if item not in {"rhythmEvidence", "etiologySupport"}
    ]

    if required_ids:
        covered_required = sum(
            1
            for identifier in required_ids
            if (assessment.get("requirementCoverage") or {}).get(
                identifier,
                False,
            )
        )
        context_ratio = covered_required / len(required_ids)
    else:
        context_ratio = 1.0 if displayable else 0.0

    context_score = round(30 * context_ratio)

    minimum_uncertainty = int(
        assessment.get("minimumUncertaintyItems") or 0
    )
    actual_uncertainty = int(
        assessment.get("uncertaintyItemCount") or 0
    )
    if minimum_uncertainty <= 0:
        uncertainty_score = 10
    else:
        uncertainty_score = round(
            10
            * min(
                1.0,
                actual_uncertainty / minimum_uncertainty,
            )
        )

    distractor_score = (
        5
        if displayable and not unsupported and not forbidden
        else 0
    )

    quality_errors = list(validation.get("qualityErrors") or [])
    quality_penalty = min(10, 3 * len(quality_errors))

    subtotal = (
        diagnosis_score
        + etiology_score
        + context_score
        + uncertainty_score
        + distractor_score
    )
    total = max(0, subtotal - quality_penalty)

    if total >= 90:
        grade = "excellent"
    elif total >= 80:
        grade = "pass"
    elif total >= 70:
        grade = "needs_improvement"
    else:
        grade = "fail"

    requirement_coverage = assessment.get("requirementCoverage") or {}

    return {
        "schemaVersion": SCORE_SCHEMA,
        "scorePurpose": "prompt-visible grounded completeness",
        "hiddenAnswerKeyUsed": False,
        "diagnosisConsistency": diagnosis_score,
        "supportedEtiology": etiology_score,
        "requiredContextCoverage": context_score,
        "uncertaintyCoverage": uncertainty_score,
        "avoidsUnsupportedClaims": distractor_score,
        "qualityPenalty": quality_penalty,
        "subtotalBeforePenalty": subtotal,
        "total": total,
        "grade": grade,
        "pass": bool(total >= 80 and displayable),
        "groundingDisplayable": displayable,
        "eligibleCoverageItems": list(
            assessment.get("requiredCoverageIds") or []
        ),
        "matchedCoverageItems": [
            key
            for key, value in requirement_coverage.items()
            if value
        ],
        "missingCoverageItems": [
            key
            for key, value in requirement_coverage.items()
            if not value
        ],
        "uncertaintyItemCount": actual_uncertainty,
        "minimumUncertaintyItems": minimum_uncertainty,
        "qualityErrors": quality_errors,
    }
