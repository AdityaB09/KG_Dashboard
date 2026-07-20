from __future__ import annotations

import json
from typing import Any


WIDGET_SYSTEM_PROMPT = """
You are a clinical evidence summarization assistant operating in research mode.

Use only the supplied structured evidence. Do not invent symptoms, diagnoses,
measurements, dates, medication exposure, current stability, P-wave findings,
ST-T findings, or causal relationships.

Keep deterministic ECG measurements, dataset annotations, FHIR context,
historical evidence, and missing evidence separate.

MedicationRequest means ordered only. It is not proof of administration,
dispensing, adherence, or episode-time exposure.

Do not control alert severity, timestamps, duration, numeric measurements,
current-state codes, medication-exposure codes, or clinical actions. Those are
owned by deterministic backend code.

Return one JSON object. Keep the existing Phase 7 fields for compatibility and
also return a widgetInterpretation object containing narrative fields only.
""".strip()


def build_widget_compatible_messages(
    evidence_package: dict[str, Any],
) -> list[dict[str, str]]:
    user_prompt = {
        "task": (
            "Summarize the supplied incident evidence for the "
            "Critical Alerts and Interpretation widget."
        ),
        "requiredResponse": {
            "evidenceSummary": "",
            "ecgFindings": [],
            "clinicallyRelevantContext": [],
            "contradictionsAndUncertainty": [],
            "missingEvidence": [],
            "suggestedClinicalReview": [],
            "safetyStatement": "",
            "widgetInterpretation": {
                "headline": "",
                "episodeNarrative": "",
                "arrhythmiaNarrative": "",
                "morphologyNarrative": "",
                "currentSituationNarrative": "",
                "rootCauseNarrative": "",
                "importantFindings": [],
                "importantLimitations": [],
            },
        },
        "rules": [
            "Do not create or alter severity, timestamps, duration, or measurements.",
            "Do not claim a confirmed diagnosis or root cause.",
            "Treat historical and remote data as background only.",
            "Treat MedicationRequest as an order only.",
            (
                "Do not claim current stability or ongoing arrhythmia "
                "without episode-near evidence."
            ),
            (
                "Keep reference V annotations separate from independent "
                "morphology candidates."
            ),
            "Return JSON only.",
        ],
        "evidencePackage": evidence_package,
    }

    return [
        {
            "role": "system",
            "content": WIDGET_SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": json.dumps(
                user_prompt,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
        },
    ]
