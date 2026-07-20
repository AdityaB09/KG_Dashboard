from __future__ import annotations

import json
import re
from typing import Any


_FORBIDDEN_PATTERNS = [
    re.compile(r"\bconfirmed (diagnosis|arrhythmia|cause)\b", re.I),
    re.compile(r"\bdefinitive (diagnosis|cause)\b", re.I),
    re.compile(r"\bcaused by\b", re.I),
    re.compile(r"\bthe patient is (stable|unstable)\b", re.I),
    re.compile(r"\bthe patient is taking\b", re.I),
    re.compile(r"\bmedication adherence is confirmed\b", re.I),
]

_UNSUPPORTED_ECG_PATTERNS = [
    re.compile(r"\bsinus rhythm\b", re.I),
    re.compile(r"\bp[- ]wave\b", re.I),
    re.compile(r"\bst[- ]segment\b", re.I),
    re.compile(r"\bt[- ]wave\b", re.I),
]

_TREATMENT_PATTERNS = [
    re.compile(
        r"\b(administer|start|stop|increase|decrease|prescribe)\b",
        re.I,
    ),
]


def parse_model_payload(
    stored_response: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(stored_response, dict):
        return {}, {
            "available": False,
            "validationStatus": "not_available",
        }

    metadata = {
        "available": True,
        "modelAlias": (
            stored_response.get("modelVariant")
            or stored_response.get("modelAlias")
        ),
        "model": stored_response.get("model"),
        "validationStatus": (
            stored_response.get("validationStatus") or "unknown"
        ),
        "validationMode": stored_response.get("validationMode"),
        "notForClinicalUse": stored_response.get("notForClinicalUse"),
        "warnings": list(stored_response.get("warnings", []) or []),
    }

    payload: Any = (
        stored_response.get("response")
        or stored_response.get("result")
        or stored_response.get("content")
        or stored_response
    )

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return {}, {
                **metadata,
                "validationStatus": "invalid_json",
            }

    if not isinstance(payload, dict):
        return {}, {
            **metadata,
            "validationStatus": "invalid_payload",
        }

    return payload, metadata


def safe_text(
    value: Any,
    *,
    allow_ecg_terms: bool = False,
    allow_treatment_language: bool = False,
) -> str | None:
    if not isinstance(value, str):
        return None

    text = " ".join(value.split()).strip()
    if not text:
        return None

    if any(pattern.search(text) for pattern in _FORBIDDEN_PATTERNS):
        return None

    if (
        not allow_ecg_terms
        and any(
            pattern.search(text)
            for pattern in _UNSUPPORTED_ECG_PATTERNS
        )
    ):
        return None

    if (
        not allow_treatment_language
        and any(
            pattern.search(text)
            for pattern in _TREATMENT_PATTERNS
        )
    ):
        return None

    return text[:1800]


def safe_string_list(
    value: Any,
    *,
    maximum: int,
    allow_ecg_terms: bool = False,
) -> list[str]:
    if isinstance(value, str):
        source = [value]
    elif isinstance(value, list):
        source = value
    else:
        return []

    output: list[str] = []

    for item in source:
        if isinstance(item, dict):
            item = (
                item.get("text")
                or item.get("detail")
                or item.get("summary")
                or item.get("reason")
            )

        text = safe_text(
            item,
            allow_ecg_terms=allow_ecg_terms,
        )

        if text and text not in output:
            output.append(text)

        if len(output) >= maximum:
            break

    return output


def normalize_confidence(value: Any) -> float | None:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None

    if confidence <= 1:
        confidence *= 100

    return max(0.0, min(100.0, confidence))


def validated_contributors(
    payload: dict[str, Any],
    *,
    episode_near_medication_administration_count: int,
    maximum: int = 2,
) -> list[dict[str, Any]]:
    candidates: Any = (
        payload.get("possibleContributors")
        or payload.get("rootCauseAssessment", {}).get("candidates")
        or []
    )

    if not isinstance(candidates, list):
        return []

    output = []

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue

        title = safe_text(
            candidate.get("title")
            or candidate.get("name")
            or candidate.get("hypothesis")
            or candidate.get("label")
        )
        if not title:
            continue

        evidence_for = safe_string_list(
            candidate.get("evidenceFor"),
            maximum=3,
            allow_ecg_terms=True,
        )
        evidence_against = safe_string_list(
            candidate.get("evidenceAgainst"),
            maximum=3,
            allow_ecg_terms=True,
        )

        if not evidence_against:
            continue

        temporal_fit = str(
            candidate.get("temporalFit")
            or candidate.get("temporalBucket")
            or "unconfirmed"
        ).strip().lower()

        confidence = normalize_confidence(
            candidate.get("confidence")
            or candidate.get("conclusionConfidence")
        )
        if confidence is None:
            confidence = 20.0

        if temporal_fit in {
            "historical",
            "historical_remote",
            "background_only",
            "unconfirmed",
            "unknown",
        }:
            confidence = min(confidence, 35.0)

        medication_related = (
            "medication" in title.lower()
            or "drug" in title.lower()
        )

        if (
            medication_related
            and episode_near_medication_administration_count == 0
        ):
            confidence = min(confidence, 25.0)
            warning = (
                "Episode-near medication administration or confirmed "
                "exposure is unavailable."
            )
            if warning not in evidence_against:
                evidence_against.append(warning)

        output.append(
            {
                "title": title,
                "confidenceScore": round(confidence, 1),
                "confidenceLabel": (
                    "low" if confidence < 40 else "moderate"
                ),
                "temporalFit": temporal_fit,
                "evidenceFor": evidence_for,
                "evidenceAgainst": evidence_against,
                "verificationNeeded": safe_string_list(
                    candidate.get("verificationNeeded")
                    or candidate.get("monitoringOrVerificationNeeded"),
                    maximum=2,
                ),
            }
        )

        if len(output) >= maximum:
            break

    return output
