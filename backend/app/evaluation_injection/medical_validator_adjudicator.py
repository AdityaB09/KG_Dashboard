from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


ADJUDICATOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": [
                "deterministic_review_sufficient",
                "human_review_needed",
                "candidate_unsafe",
            ],
        },
        "issueReviews": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issueIndex": {"type": "integer"},
                    "verdict": {
                        "type": "string",
                        "enum": [
                            "validator_supported",
                            "validator_false_positive",
                            "uncertain",
                        ],
                    },
                    "rationale": {"type": "string"},
                    "evidencePaths": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "issueIndex",
                    "verdict",
                    "rationale",
                    "evidencePaths",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["decision", "issueReviews"],
    "additionalProperties": False,
}


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _issue_evidence(
    issue: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    normalized = issue.lower()
    event = evidence.get("controlledEventContext") or {}
    oracle = evidence.get("oracleContext") or {}

    packet: dict[str, Any] = {
        "sourceManifest": evidence.get("sourceManifest") or {},
        "missingEvidence": evidence.get("missingEvidence") or [],
    }

    if "electrolyte" in normalized:
        packet["controlledEventElectrolytes"] = (
            event.get("electrolytes") or {}
        )

    if "infection" in normalized or "sepsis" in normalized:
        packet["controlledEventInfection"] = (
            event.get("infection") or {}
        )

    if (
        "renal" in normalized
        or "kidney" in normalized
        or "ckd" in normalized
    ):
        packet["controlledEventRenal"] = (
            event.get("renal") or {}
        )
        packet["oracleConditionAvailability"] = (
            oracle.get("resourceAvailability") or {}
        )

    if "medication" in normalized or "exposure" in normalized:
        packet["oracleMedications"] = list(
            oracle.get("medications") or []
        )[:8]

    if (
        "historical" in normalized
        or "temporal" in normalized
        or "current" in normalized
    ):
        packet["oracleLabTrends"] = list(
            oracle.get("labTrends") or []
        )[:6]
        packet["oracleVitalTrends"] = list(
            oracle.get("vitalTrends") or []
        )[:6]

    if "chest pain" in normalized or "ischemi" in normalized:
        packet["controlledEventIschemia"] = (
            event.get("ischemia") or {}
        )

    return packet


def _messages(
    *,
    response: dict[str, Any],
    validation: dict[str, Any],
    evidence: dict[str, Any],
) -> list[dict[str, str]]:
    quality_errors = list(
        validation.get("qualityErrors") or []
    )

    issues = [
        {
            "issueIndex": index,
            "validatorIssue": issue,
            "relevantEvidence": _issue_evidence(
                issue,
                evidence,
            ),
        }
        for index, issue in enumerate(quality_errors)
    ]

    system = """
You are an advisory reviewer of deterministic validator issues.

You are NOT being asked to summarize the patient, medications, laboratory
results, or episode. Review only the listed validator issues against the
candidate response and the issue-specific evidence.

Rules:
1. Do not introduce any new clinical claim.
2. Do not list a medication, condition, measurement, or timing value unless it
   already appears in the candidate response or the specific issue packet.
3. MedicationRequest means an order only, not administration or current use.
4. Historical or future observations are not current episode-time physiology.
5. Missing Oracle conditions do not prove disease absence.
6. Your output is advisory and cannot override deterministic hard errors.
7. Return one issueReviews entry for every supplied issueIndex.
8. Keep each rationale under 240 characters.

Return only the requested JSON object.
""".strip()

    user = {
        "candidateResponse": response,
        "issues": issues,
    }

    return [
        {
            "role": "system",
            "content": system,
        },
        {
            "role": "user",
            "content": json.dumps(
                user,
                ensure_ascii=False,
            ),
        },
    ]


def _parse_content(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value

    text = str(value or "").strip()
    if not text:
        raise ValueError(
            "Medical validator returned empty content."
        )

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(
            text[start : end + 1]
        )

    if not isinstance(parsed, dict):
        raise ValueError(
            "Medical validator response was not a JSON object."
        )

    return parsed


def _validate_review(
    review: dict[str, Any],
    issue_count: int,
) -> dict[str, Any]:
    issue_reviews = review.get("issueReviews")
    if not isinstance(issue_reviews, list):
        raise ValueError(
            "Medical validator issueReviews was not a list."
        )

    expected = set(range(issue_count))
    received: set[int] = set()

    for item in issue_reviews:
        if not isinstance(item, dict):
            raise ValueError(
                "Medical validator issue review was not an object."
            )

        index = item.get("issueIndex")
        if not isinstance(index, int):
            raise ValueError(
                "Medical validator issueIndex was not an integer."
            )
        if index not in expected:
            raise ValueError(
                f"Medical validator returned unknown issueIndex {index}."
            )
        if index in received:
            raise ValueError(
                f"Medical validator duplicated issueIndex {index}."
            )
        received.add(index)

        rationale = str(
            item.get("rationale") or ""
        ).strip()
        if len(rationale) > 240:
            item["rationale"] = rationale[:240]

    if received != expected:
        raise ValueError(
            "Medical validator did not review every deterministic issue."
        )

    verdicts = {
        str(item.get("verdict") or "")
        for item in issue_reviews
        if isinstance(item, dict)
    }

    model_proposed_decision = review.get(
        "decision"
    )

    if "uncertain" in verdicts:
        computed_decision = (
            "human_review_needed"
        )
    else:
        computed_decision = (
            "deterministic_review_sufficient"
        )

    review[
        "modelProposedDecision"
    ] = model_proposed_decision
    review["decision"] = computed_decision

    return review


def public_medical_review(
    review: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Return only safe metadata for widget DTOs.

    The raw advisory rationale remains in medical_validator_review.json but is
    never copied into the clinical widget.
    """
    value = review or {}
    parsed_review = value.get("review") or {}
    issue_reviews = parsed_review.get("issueReviews") or []

    return {
        "status": value.get("status"),
        "advisoryOnly": True,
        "model": value.get("model"),
        "decision": parsed_review.get("decision"),
        "issueReviewCount": len(issue_reviews),
        "validatorFalsePositiveCount": sum(
            1
            for item in issue_reviews
            if (
                isinstance(item, dict)
                and item.get("verdict")
                == "validator_false_positive"
            )
        ),
    }


async def run_medical_validator_review(
    *,
    response: dict[str, Any],
    validation: dict[str, Any],
    evidence: dict[str, Any],
    artifact_path: Path | None = None,
) -> dict[str, Any]:
    """
    Run an optional issue-specific medical review.

    The deterministic validator remains authoritative. This reviewer cannot
    convert a deterministic rejection into acceptance.
    """
    if not _flag(
        "SLM_MEDICAL_VALIDATOR_ENABLED",
        False,
    ):
        return {
            "schemaVersion": (
                "medical-validator-review-v2"
            ),
            "status": "disabled",
            "advisoryOnly": True,
        }

    quality_errors = list(
        validation.get("qualityErrors") or []
    )
    hard_errors = list(
        validation.get("hardErrors") or []
    )

    if hard_errors:
        return {
            "schemaVersion": (
                "medical-validator-review-v2"
            ),
            "status": "skipped_hard_rejection",
            "advisoryOnly": True,
            "hardErrorCount": len(hard_errors),
        }

    if not quality_errors:
        return {
            "schemaVersion": (
                "medical-validator-review-v2"
            ),
            "status": "not_applicable",
            "advisoryOnly": True,
        }

    endpoint = os.getenv(
        "SLM_MEDICAL_VALIDATOR_ENDPOINT",
        "http://127.0.0.1:11434/api/chat",
    ).strip()
    model = os.getenv(
        "SLM_MEDICAL_VALIDATOR_MODEL",
        "medgemma1.5:4b",
    ).strip()
    timeout = float(
        os.getenv(
            "SLM_MEDICAL_VALIDATOR_TIMEOUT_SECONDS",
            "420",
        )
    )
    max_tokens = int(
        os.getenv(
            "SLM_MEDICAL_VALIDATOR_MAX_TOKENS",
            "500",
        )
    )

    request = {
        "model": model,
        "messages": _messages(
            response=response,
            validation=validation,
            evidence=evidence,
        ),
        "stream": False,
        "format": ADJUDICATOR_SCHEMA,
        "options": {
            "temperature": 0,
            "num_predict": max_tokens,
        },
    }

    created_at = _now_iso()

    try:
        async with httpx.AsyncClient(
            timeout=timeout
        ) as client:
            result = await client.post(
                endpoint,
                json=request,
            )
            result.raise_for_status()
            payload = result.json()

        message = payload.get("message") or {}
        parsed = _parse_content(
            message.get("content")
        )
        reviewed = _validate_review(
            parsed,
            len(quality_errors),
        )

        output = {
            "schemaVersion": (
                "medical-validator-review-v2"
            ),
            "status": "complete",
            "createdAt": created_at,
            "advisoryOnly": True,
            "model": model,
            "endpoint": endpoint,
            "review": reviewed,
        }

    except Exception as exc:
        output = {
            "schemaVersion": (
                "medical-validator-review-v2"
            ),
            "status": "failed",
            "createdAt": created_at,
            "advisoryOnly": True,
            "model": model,
            "endpoint": endpoint,
            "error": (
                f"{type(exc).__name__}: {exc}"
            ),
        }

    if artifact_path is not None:
        artifact_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        temporary = artifact_path.with_suffix(
            artifact_path.suffix + ".tmp"
        )
        temporary.write_text(
            json.dumps(
                output,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(artifact_path)

    return output
