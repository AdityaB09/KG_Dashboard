from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Mapping, Sequence

from app.phase7.config import (
    phase7_settings,
)


SYSTEM_PROMPT = """
You are a clinical evidence summarization assistant operating in research mode.

Use only the supplied structured evidence. Do not invent measurements, patient facts, medications, laboratory results, timing, or diagnoses.

Always distinguish:
1. independently measured deterministic ECG evidence,
2. PhysioNet INCART dataset reference annotations,
3. Oracle/FHIR clinical context,
4. historical or temporally remote clinical data,
5. missing or unavailable evidence.

A dataset reference annotation is not an independent diagnosis. A high deterministic-evidence confidence score is not diagnostic confidence. Do not claim that historical Oracle sandbox data caused an INCART ECG event. Do not claim that the INCART waveform and Oracle FHIR data came from the same real patient.

When evidence conflicts or measures different concepts, state the distinction instead of choosing one value. Respect every limitation and confidence reduction. Never claim that missing data is normal.

Return JSON with these keys:
- evidenceSummary
- ecgFindings
- clinicallyRelevantContext
- contradictionsAndUncertainty
- missingEvidence
- suggestedClinicalReview
- safetyStatement

The output must remain an evidence-grounded research summary and must not be an independent diagnosis.
""".strip()


USER_INSTRUCTION = """
Review the supplied incident evidence package.

Summarize the strongest supported ECG evidence first. Then include only clinically relevant medications, laboratory trends, vital trends, conditions, encounters, reports, and documents. Clearly state their temporal relationship to the incident anchor. Treat historical or remote observations as background only.

Do not reinterpret an independent abnormal-morphology-candidate percentage as the total PVC burden. Keep dataset V annotations separate from independent morphology candidates.

Return the required JSON object only.
""".strip()


# These are actual data-bearing keys that must never appear in the
# Phase 7 prompt package. Matching is exact after normalization.
#
# Safe metadata keys such as:
#   waveformSource
#   rawWaveformsIncluded
#   rawWaveformsStored
# are intentionally NOT blocked.
_BLOCKED_EXACT_KEYS = {
    "rawmv",
    "centeredmv",
    "waveforms",
    "waveformarrays",
    "rawwaveformarrays",
    "filteredsignals",
    "filteredsamples",
    "beatarrays",
    "signals",
    "samples",
    "samplearrays",
    "ecgsamples",
    "ppgsamples",
}


# A few data containers may use descriptive suffixes. These suffixes
# are blocked only when the full normalized key clearly identifies
# an array/sample payload. General metadata containing "waveform"
# is not rejected.
_BLOCKED_KEY_SUFFIXES = (
    "waveformarray",
    "waveformarrays",
    "signalarray",
    "signalarrays",
    "samplearray",
    "samplearrays",
    "beatarray",
    "beatarrays",
)


_SAFE_METADATA_KEYS = {
    "waveformsource",
    "rawwaveformsincluded",
    "rawwaveformsstored",
    "waveformavailable",
    "waveformavailability",
    "waveformstoragepath",
    "waveformfingerprint",
    "waveformhash",
}


def _normalize_key(
    key: Any,
) -> str:
    return "".join(
        character
        for character in str(key).lower()
        if character.isalnum()
    )


def _is_blocked_payload_key(
    key: Any,
) -> bool:
    normalized = _normalize_key(key)

    if normalized in _SAFE_METADATA_KEYS:
        return False

    if normalized in _BLOCKED_EXACT_KEYS:
        return True

    return any(
        normalized.endswith(suffix)
        for suffix in _BLOCKED_KEY_SUFFIXES
    )


def validate_evidence(
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    detected_keys: list[str] = []
    oversized_arrays: list[str] = []

    def inspect(
        value: Any,
        path: str,
    ) -> None:
        if isinstance(
            value,
            Mapping,
        ):
            for key, child in value.items():
                child_path = (
                    f"{path}.{key}"
                )

                if _is_blocked_payload_key(
                    key
                ):
                    detected_keys.append(
                        child_path
                    )

                inspect(
                    child,
                    child_path,
                )

        elif isinstance(
            value,
            Sequence,
        ) and not isinstance(
            value,
            (
                str,
                bytes,
                bytearray,
            ),
        ):
            if len(value) > 100:
                oversized_arrays.append(
                    f"{path}[{len(value)}]"
                )

            for index, child in enumerate(
                value
            ):
                inspect(
                    child,
                    f"{path}[{index}]",
                )

    inspect(
        evidence,
        "$",
    )

    safety = (
        evidence.get("safety")
        if isinstance(
            evidence.get("safety"),
            Mapping,
        )
        else {}
    )

    independent = safety.get(
        "isIndependentDiagnosis"
    )

    raw_waveforms_included = (
        safety.get(
            "rawWaveformsIncluded"
        )
    )

    ready = bool(
        not detected_keys
        and not oversized_arrays
        and independent is False
        and raw_waveforms_included
        is not True
    )

    return {
        "status": (
            "ready"
            if ready
            else "failed"
        ),
        "rawWaveformKeys": (
            detected_keys
        ),
        "oversizedArrays": (
            oversized_arrays
        ),
        "isIndependentDiagnosis": (
            independent
        ),
        "rawWaveformsIncluded": (
            raw_waveforms_included
        ),
    }


def _serialized_evidence(
    evidence: Mapping[str, Any],
) -> tuple[
    str,
    bool,
]:
    payload = json.dumps(
        evidence,
        indent=2,
        ensure_ascii=False,
        default=str,
    )

    maximum = (
        phase7_settings
        .maximum_prompt_characters
    )

    if len(payload) <= maximum:
        return (
            payload,
            False,
        )

    compact = deepcopy(
        dict(evidence)
    )

    clinical = (
        compact.get("evidence", {})
        .get("clinicalContext", {})
    )

    if isinstance(
        clinical,
        dict,
    ):
        for key, limit in (
            ("documents", 5),
            ("diagnosticReports", 5),
            ("encounters", 5),
            ("medicationTimeline", 10),
            ("labTrends", 8),
            ("vitalTrends", 8),
        ):
            value = clinical.get(key)

            if isinstance(
                value,
                list,
            ):
                clinical[key] = value[
                    :limit
                ]

    episodes = (
        compact.get("evidence", {})
        .get(
            "independentlyMeasuredEcg",
            {},
        )
        .get(
            "episodeSummaries"
        )
    )

    if isinstance(
        episodes,
        list,
    ):
        compact[
            "evidence"
        ][
            "independentlyMeasuredEcg"
        ][
            "episodeSummaries"
        ] = episodes[:4]

    payload = json.dumps(
        compact,
        separators=(
            ",",
            ":",
        ),
        ensure_ascii=False,
        default=str,
    )

    if len(payload) > maximum:
        payload = payload[
            :maximum
        ]

    return (
        payload,
        True,
    )


def build_prompt_package(
    evidence: Mapping[str, Any],
    *,
    schema_version: str,
) -> dict[str, Any]:
    validation = validate_evidence(
        evidence
    )

    if (
        validation.get("status")
        != "ready"
    ):
        raise ValueError(
            "Phase 7 evidence failed "
            f"safety validation: "
            f"{validation}"
        )

    serialized, truncated = (
        _serialized_evidence(
            evidence
        )
    )

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": (
                f"{USER_INSTRUCTION}\n\n"
                "EVIDENCE_PACKAGE_JSON:\n"
                f"{serialized}"
            ),
        },
    ]

    total_characters = sum(
        len(
            str(
                item.get("content")
                or ""
            )
        )
        for item in messages
    )

    return {
        "schemaVersion": (
            schema_version
        ),
        "incidentId": (
            evidence.get(
                "incidentId"
            )
        ),
        "promptMode": (
            evidence.get(
                "promptMode"
            )
        ),
        "messages": messages,
        "promptText": "\n\n".join(
            [
                (
                    "SYSTEM:\n"
                    f"{SYSTEM_PROMPT}"
                ),
                (
                    "USER:\n"
                    f"{USER_INSTRUCTION}"
                    "\n\n"
                    "EVIDENCE_PACKAGE_JSON:\n"
                    f"{serialized}"
                ),
            ]
        ),
        "validation": {
            **validation,
            "evidenceWasTruncated": (
                truncated
            ),
            "promptCharacters": (
                total_characters
            ),
            "estimatedPromptTokens": (
                round(
                    total_characters
                    / 4
                )
            ),
        },
        "generationControls": {
            "temperature": 0.0,
            "responseFormat": "json",
            "allowIndependentDiagnosis": (
                False
            ),
        },
    }
