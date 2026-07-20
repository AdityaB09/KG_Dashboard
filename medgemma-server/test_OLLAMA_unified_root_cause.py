from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

import requests
from tqdm import tqdm


# =============================================================================
# Paths and Ollama settings
# =============================================================================

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
INPUT_FILE = SCRIPT_DIRECTORY / "SLM_Input_Package.json"
OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"

AVAILABLE_MODELS: dict[str, str] = {
    # MedGemma
    "medgemma27": "medgemma:27b",
    "q8": "medgemma1.5:4b-it-q8_0",
    "bf16": "medgemma1.5:4b-it-bf16",
    "medgemma4_q4": "hf.co/mradermacher/medgemma-4b-it-GGUF:Q4_K_M",

    # Biomedical / medical models already used in this project
    "biomistral": "cniongolo/biomistral",
    "biomistral_q5": "hf.co/gguf/BioMistral-7B-GGUF:Q5_K_M",
    "openbiollm_q5": "koesn/llama3-openbiollm-8b:q5_K_M",
    "openbiollm": "charlestang06/openbiollm",
    "huatuo7": "hf.co/QuantFactory/HuatuoGPT-o1-7B-GGUF:Q5_K_M",
    "huatuo8": "hf.co/QuantFactory/HuatuoGPT-o1-8B-GGUF:Q5_K_M",
    "apollo": "hf.co/FreedomIntelligence/Apollo-7B-GGUF:Q4_K_M",
    "mistral_nemo": "mistral-nemo:12b-instruct-2407-q4_K_M",
    "llama31": "llama3.1:8b",

    # Additional medical models
    "meditron3": "hf.co/QuantFactory/Meditron3-8B-GGUF:Q4_K_M",
    "medreason8": "hf.co/SuperMaker/MedReason-8B-GGUF:Q4_K_M",
    "medical_qwen25": (
        "hf.co/mradermacher/Medical-Qwen2.5-7B-Instruct-GGUF:Q4_K_M"
    ),
    "med_qwen2": "hf.co/mradermacher/Med-Qwen2-7B-GGUF:Q4_K_M",
    "meerkat7": "hf.co/mradermacher/meerkat-7b-v1.0-GGUF:Q4_K_M",
    "mediphi": "hf.co/surya-ravindra/MediPhi-Instruct-Q4_K_M-GGUF:Q4_K_M",
    "clinical_llama_v21": (
        "hf.co/mradermacher/llama-3.1-8b-clinical-V2.1-GGUF:Q4_K_M"
    ),
    "clinical_llama_v20": (
        "hf.co/mradermacher/llama-3.1-8b-clinical-V2.0-GGUF:Q4_K_M"
    ),
    "clinical_llama_v14": (
        "hf.co/mradermacher/llama-3.1-8b-clinical-V1.4-GGUF:Q4_K_M"
    ),
}

DEFAULT_CONTEXT_SIZE = 16384
DEFAULT_MAX_OUTPUT_TOKENS = 4000
MAX_OUTPUT_TOKEN_LIMIT = 7000
TOKEN_INCREMENT = 2000
DEFAULT_MAX_ATTEMPTS = 2
REQUEST_TIMEOUT_SECONDS = 7200
KEEP_ALIVE = "10m"

CASE_MODE = "synthetic_unified_case_benchmark"
OUTPUT_SCHEMA_VERSION = "phase-7-root-cause-v2"
REQUIRED_SAFETY_SENTENCE = (
    "This is an evidence-grounded research summary and not an independent diagnosis."
)


# =============================================================================
# Model-facing analytical response schema
# =============================================================================
#
# Verified ECG, medication, lab and vital facts are NOT trusted to the model.
# They are extracted directly from SLM_Input_Package.json and inserted into the
# final response by this script. The model is used for hypothesis generation,
# evidence comparison and review recommendations only.
#

MODEL_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "evidenceSummary": {"type": "string"},
        "rootCauseAssessment": {
            "type": "object",
            "properties": {
                "conclusion": {"type": "string"},
                "conclusionConfidence": {"type": "number"},
                "primaryHypothesisId": {"type": "string"},
                "candidates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "category": {"type": "string"},
                            "title": {"type": "string"},
                            "hypothesis": {"type": "string"},
                            "likelihood": {"type": "string"},
                            "causalStrength": {"type": "string"},
                            "confidence": {"type": "number"},
                            "temporalFit": {"type": "string"},
                            "evidenceFor": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "evidenceAgainst": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "sourceReferences": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "verificationNeeded": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": [
                            "id",
                            "category",
                            "title",
                            "hypothesis",
                            "likelihood",
                            "causalStrength",
                            "confidence",
                            "temporalFit",
                            "evidenceFor",
                            "evidenceAgainst",
                            "sourceReferences",
                            "verificationNeeded",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": [
                "conclusion",
                "conclusionConfidence",
                "primaryHypothesisId",
                "candidates",
            ],
            "additionalProperties": False,
        },
        "medicationRiskAssessment": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sourceId": {"type": "string"},
                    "medicationName": {"type": "string"},
                    "possibleRelevance": {"type": "string"},
                    "rationale": {"type": "string"},
                    "confidence": {"type": "number"},
                    "evidenceFor": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "evidenceAgainst": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "monitoringOrVerificationNeeded": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "sourceId",
                    "medicationName",
                    "possibleRelevance",
                    "rationale",
                    "confidence",
                    "evidenceFor",
                    "evidenceAgainst",
                    "monitoringOrVerificationNeeded",
                ],
                "additionalProperties": False,
            },
        },
        "contradictionsAndUncertainty": {
            "type": "array",
            "items": {"type": "string"},
        },
        "missingEvidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "evidenceType": {"type": "string"},
                    "reason": {"type": "string"},
                    "impactOnRootCause": {"type": "string"},
                    "priority": {"type": "string"},
                },
                "required": [
                    "evidenceType",
                    "reason",
                    "impactOnRootCause",
                    "priority",
                ],
                "additionalProperties": False,
            },
        },
        "recommendedClinicalReview": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "priority": {"type": "string"},
                    "action": {"type": "string"},
                    "rationale": {"type": "string"},
                    "basedOnEvidence": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "priority",
                    "action",
                    "rationale",
                    "basedOnEvidence",
                ],
                "additionalProperties": False,
            },
        },
        "dashboardSummary": {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "priority": {"type": "string"},
                "keyDrivers": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "reviewFlags": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "displayDisclaimer": {"type": "string"},
            },
            "required": [
                "headline",
                "priority",
                "keyDrivers",
                "reviewFlags",
                "displayDisclaimer",
            ],
            "additionalProperties": False,
        },
        "safetyStatement": {"type": "string"},
    },
    "required": [
        "evidenceSummary",
        "rootCauseAssessment",
        "medicationRiskAssessment",
        "contradictionsAndUncertainty",
        "missingEvidence",
        "recommendedClinicalReview",
        "dashboardSummary",
        "safetyStatement",
    ],
    "additionalProperties": False,
}


ROOT_CAUSE_CONTROL_MESSAGE = {
    "role": "system",
    "content": f"""
Act as a senior clinical software architect, ECG signal-processing engineer,
medical-device software engineer and technical lead designing an explainable
patient-monitoring dashboard.

This is an OFFLINE SYNTHETIC UNIFIED-CASE BENCHMARK. Treat the supplied ECG
measurements and clinical context as one hypothetical patient case for model
comparison. Do not discuss separate patients, source mismatch, cross-dataset
linkage, provenance mismatch, or the benchmark transformation in the clinical
JSON response.

Your task is NOT to declare a diagnosis. Your task is to identify and rank
possible contributors to an episodic ECG event while clearly showing evidence,
timing, uncertainty, contradictory evidence and what is needed for confirmation.

Critical rules:
1. Use only values present in the supplied package.
2. Never invent medication doses, routes, dates, laboratory values, vital signs,
   ECG measurements, diagnoses or resource identifiers.
3. MedicationRequest means an ORDER. It does not prove dispensing,
   administration, adherence or current exposure.
4. Do not say "the patient is taking" when evidenceLevel is "order". Say
   "an order exists" or "ordered-only exposure is unconfirmed".
5. Historical-remote labs and medication orders may be background or possible
   contributors, but cannot by themselves establish the root cause of the event.
6. A temporal association is not causation.
7. Only label a candidate as likely when episode-near objective evidence directly
   supports it. Otherwise use possible, unlikely or indeterminate.
8. If available evidence does not establish a cause, use the conclusion
   "no_confirmed_root_cause" or "possible_contributors_only".
9. Keep deterministic ECG measurements separate from dataset annotations.
10. A zero independent abnormal-morphology candidate count does not negate
    dataset V annotations.
11. Do not reinterpret undefined morphology or agreement scores.
12. Recommended actions must be review or verification actions. Do not prescribe,
    start, stop or change medication.
13. sourceReferences should use exact source IDs from the package when possible,
    or these canonical references: ecg.signalQuality, ecg.rhythm, ecg.qrs,
    ecg.morphology, ecg.referenceAnnotations, incident.timeline.
14. Return exactly one JSON object matching the supplied schema.
15. Return JSON only. No Markdown and no code fences.
16. Include this safety sentence in safetyStatement:
    {REQUIRED_SAFETY_SENTENCE}
""".strip(),
}


# =============================================================================
# CLI
# =============================================================================


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run one Ollama model for a synthetic unified ECG + FHIR root-cause "
            "benchmark using SLM_Input_Package.json."
        )
    )

    parser.add_argument(
        "--model",
        required=True,
        choices=AVAILABLE_MODELS.keys(),
        help="Model alias from AVAILABLE_MODELS.",
    )
    parser.add_argument(
        "--model-name",
        default=None,
        help="Exact Ollama model name. Overrides --model.",
    )
    parser.add_argument(
        "--context",
        type=int,
        default=DEFAULT_CONTEXT_SIZE,
        help=f"Ollama context size. Default: {DEFAULT_CONTEXT_SIZE}",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_OUTPUT_TOKENS,
        help=f"Initial output-token ceiling. Default: {DEFAULT_MAX_OUTPUT_TOKENS}",
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help=f"Maximum attempts. Default: {DEFAULT_MAX_ATTEMPTS}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output path.",
    )

    return parser.parse_args()


# =============================================================================
# Progress indicator
# =============================================================================


class ActivityIndicator:
    def __init__(self, description: str) -> None:
        self.description = description
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        with tqdm(
            total=100,
            desc=self.description,
            unit="%",
            dynamic_ncols=True,
            leave=True,
        ) as progress:
            position = 0
            direction = 1
            while not self.stop_event.is_set():
                position += direction * 2
                if position >= 96:
                    position = 96
                    direction = -1
                elif position <= 2:
                    position = 2
                    direction = 1
                progress.n = position
                progress.refresh()
                time.sleep(0.25)
            progress.n = 100
            progress.refresh()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2)


# =============================================================================
# Input package and synthetic unified-case preparation
# =============================================================================


CASE_LINKAGE_PHRASES = (
    "same real patient",
    "same-patient",
    "same patient verified",
    "not verified as same",
    "not verified same",
    "controlled research pairing",
    "controlled pairing",
    "research pairing",
    "different patient",
    "different people",
    "cross-source linkage",
    "source linkage",
    "provenance mismatch",
    "no unexpired oracle smart session",
)

DROP_UNIFIED_KEYS = {
    "samepatientverified",
    "clinicalcontextsource",
    "waveformsource",
    "triggersource",
    "fhirbaseurl",
    "patientid",
    "sessioncount",
}


def load_input_package() -> dict[str, Any]:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Input file was not found:\n{INPUT_FILE}\n\n"
            "Place SLM_Input_Package.json beside this script."
        )

    try:
        with INPUT_FILE.open("r", encoding="utf-8") as file:
            package = json.load(file)
    except json.JSONDecodeError as exc:
        raise ValueError(f"SLM_Input_Package.json is invalid JSON: {exc}") from exc

    if not isinstance(package, dict):
        raise ValueError("The input package root must be a JSON object.")

    return package


def validate_input_messages(package: dict[str, Any]) -> list[dict[str, str]]:
    messages = package.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("The package must contain a non-empty messages array.")

    validated: list[dict[str, str]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ValueError(f"messages[{index}] must be an object.")

        role = message.get("role")
        content = message.get("content")
        if role not in {"system", "user", "assistant"}:
            raise ValueError(f"messages[{index}] has an invalid role: {role}")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"messages[{index}].content must be non-empty text.")

        validated.append({"role": role, "content": content})

    return validated


def contains_linkage_language(value: Any) -> bool:
    text = str(value).lower()
    return any(phrase in text for phrase in CASE_LINKAGE_PHRASES)


def sanitize_instruction_text(content: str) -> str:
    cleaned_lines: list[str] = []
    for line in content.splitlines():
        if contains_linkage_language(line):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def sanitize_evidence_for_unified_case(value: Any) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, child in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized_key in DROP_UNIFIED_KEYS:
                continue

            if normalized_key == "sourceresolution":
                output[key] = {
                    "source": "synthetic_unified_case_context",
                    "status": "ready",
                }
                continue

            if normalized_key == "provenance":
                sanitized = sanitize_evidence_for_unified_case(child)
                if isinstance(sanitized, dict):
                    sanitized["caseMode"] = CASE_MODE
                output[key] = sanitized
                continue

            if normalized_key in {
                "limitations",
                "requiredoutputbehavior",
                "contradictionsanddistinctions",
                "contradictionsanduncertainty",
            } and isinstance(child, list):
                output[key] = [
                    sanitize_evidence_for_unified_case(item)
                    for item in child
                    if not (
                        isinstance(item, str)
                        and contains_linkage_language(item)
                    )
                ]
                continue

            output[key] = sanitize_evidence_for_unified_case(child)

        if isinstance(output.get("safety"), dict):
            output["safety"]["caseMode"] = CASE_MODE
            output["safety"]["isIndependentDiagnosis"] = False

        return output

    if isinstance(value, list):
        return [
            sanitize_evidence_for_unified_case(item)
            for item in value
            if not (isinstance(item, str) and contains_linkage_language(item))
        ]

    if isinstance(value, str):
        return sanitize_instruction_text(value)

    return value


def extract_evidence_json(messages: list[dict[str, str]]) -> dict[str, Any]:
    marker = "EVIDENCE_PACKAGE_JSON:"
    for message in messages:
        if message["role"] != "user" or marker not in message["content"]:
            continue

        json_text = message["content"].split(marker, 1)[1].strip()
        try:
            evidence = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"The EVIDENCE_PACKAGE_JSON block is invalid JSON: {exc}"
            ) from exc

        if not isinstance(evidence, dict):
            raise ValueError("EVIDENCE_PACKAGE_JSON must be a JSON object.")
        return evidence

    raise ValueError("No EVIDENCE_PACKAGE_JSON block was found in messages.")


def prepare_model_messages(
    original_messages: list[dict[str, str]],
    sanitized_evidence: dict[str, Any],
    previous_errors: list[str] | None = None,
) -> list[dict[str, str]]:
    marker = "EVIDENCE_PACKAGE_JSON:"
    prepared: list[dict[str, str]] = []

    for message in original_messages:
        role = message["role"]
        content = message["content"]

        if role == "user" and marker in content:
            instruction = content.split(marker, 1)[0]
            instruction = sanitize_instruction_text(instruction)
            content = (
                f"{instruction}\n\n{marker}\n"
                + json.dumps(
                    sanitized_evidence,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        else:
            content = sanitize_instruction_text(content)

        if content.strip():
            prepared.append({"role": role, "content": content})

    if prepared:
        prepared.insert(1, ROOT_CAUSE_CONTROL_MESSAGE)
    else:
        prepared.append(ROOT_CAUSE_CONTROL_MESSAGE)

    if previous_errors:
        prepared.append(
            {
                "role": "system",
                "content": (
                    "The previous output was unusable JSON. Regenerate the complete "
                    "answer using the same evidence and correct only these problems:\n"
                    + "\n".join(f"- {error}" for error in previous_errors)
                    + "\nReturn one complete JSON object only."
                ),
            }
        )

    return prepared


# =============================================================================
# Deterministic verified-fact extraction
# =============================================================================


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def string_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def array_of_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        result = [string_value(item) for item in value]
        return [item for item in result if item]
    text = string_value(value)
    return [text] if text else []


def deduplicate_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = re.sub(r"\s+", " ", item).strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(item.strip())
    return result


def clean_model_text(value: Any) -> str:
    """Remove source-mismatch language and unsupported adherence wording."""
    text = string_value(value)
    if not text:
        return ""

    text = re.sub(
        r"\bthe patient is currently taking\b",
        "an order exists for",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bthe patient is taking\b",
        "an order exists for",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bthe patient takes\b",
        "an order exists for",
        text,
        flags=re.IGNORECASE,
    )

    # Drop complete sentences that discuss source mismatch. Unified benchmark mode
    # should present the supplied data as one hypothetical case.
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept = [
        sentence.strip()
        for sentence in sentences
        if sentence.strip() and not contains_linkage_language(sentence)
    ]
    return " ".join(kept).strip()


def clean_model_strings(value: Any) -> list[str]:
    return deduplicate_strings(
        [clean_model_text(item) for item in array_of_strings(value) if clean_model_text(item)]
    )


def simplify_medication_name(name: str) -> str:
    cleaned = name.strip()
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = re.sub(
            r"^template non-formulary \(medication\) \((.*)\)$",
            r"\1",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()
    return cleaned


def medication_exposure_status(resource_type: str) -> str:
    mapping = {
        "MedicationAdministration": "administered",
        "MedicationStatement": "reported_taken",
        "MedicationDispense": "dispensed",
        "MedicationRequest": "ordered_only",
    }
    return mapping.get(resource_type, "unknown")


def compact_unique_points(points: list[Any]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    result: list[dict[str, Any]] = []

    for point in points:
        if not isinstance(point, dict):
            continue
        key = (
            point.get("resourceId"),
            point.get("value"),
            point.get("unit"),
            point.get("observedAt"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "sourceId": point.get("resourceId"),
                "value": point.get("value"),
                "unit": point.get("unit"),
                "observedAt": point.get("observedAt"),
                "status": point.get("status"),
                "minutesFromAnchor": point.get("minutesFromAnchor"),
                "relation": point.get("relation"),
                "relationLabel": point.get("relationLabel"),
                "temporalBucket": point.get("temporalBucket"),
            }
        )

    return result


def build_verified_facts(
    evidence: dict[str, Any],
    package: dict[str, Any],
) -> dict[str, Any]:
    evidence_section = as_dict(evidence.get("evidence"))
    ecg = as_dict(evidence_section.get("independentlyMeasuredEcg"))
    dataset_reference = as_dict(evidence_section.get("datasetReference"))
    clinical = as_dict(evidence_section.get("clinicalContext"))
    incident = as_dict(evidence.get("incident"))

    medications: list[dict[str, Any]] = []
    for item in as_list(clinical.get("medicationTimeline")):
        if not isinstance(item, dict):
            continue

        resource_type = string_value(item.get("resourceType"))
        exposure_status = medication_exposure_status(resource_type)
        medications.append(
            {
                "sourceId": string_value(item.get("id")),
                "medicationName": simplify_medication_name(
                    string_value(item.get("name"))
                ),
                "resourceType": resource_type,
                "orderStatus": item.get("status"),
                "evidenceLevel": item.get("evidenceLevel"),
                "exposureStatus": exposure_status,
                "dose": {
                    "value": item.get("doseValue"),
                    "unit": item.get("doseUnit"),
                    "display": item.get("doseDisplay"),
                    "route": item.get("route"),
                    "instructions": item.get("instructions"),
                },
                "eventTime": item.get("eventTime"),
                "minutesFromAnchor": item.get("minutesFromAnchor"),
                "relation": item.get("relation"),
                "relationLabel": item.get("relationLabel"),
                "temporalBucket": item.get("temporalBucket"),
                "dashboardWarning": (
                    "Order only; dispensing, administration and adherence are not confirmed."
                    if exposure_status == "ordered_only"
                    else ""
                ),
            }
        )

    labs: list[dict[str, Any]] = []
    for trend in as_list(clinical.get("labTrends")):
        if not isinstance(trend, dict):
            continue
        labs.append(
            {
                "field": trend.get("field"),
                "label": trend.get("label"),
                "latestValue": trend.get("latestValue"),
                "unit": trend.get("unit"),
                "latestAt": trend.get("latestAt"),
                "minutesFromAnchor": (
                    compact_unique_points(as_list(trend.get("points")))[-1].get(
                        "minutesFromAnchor"
                    )
                    if compact_unique_points(as_list(trend.get("points")))
                    else None
                ),
                "relation": trend.get("latestRelation"),
                "relationLabel": trend.get("latestRelationLabel"),
                "temporalBucket": trend.get("temporalBucket"),
                "trendDirection": trend.get("trendDirection"),
                "classification": trend.get("classification"),
                "points": compact_unique_points(as_list(trend.get("points"))),
            }
        )

    vitals: list[dict[str, Any]] = []
    for trend in as_list(clinical.get("vitalTrends")):
        if not isinstance(trend, dict):
            continue

        points = compact_unique_points(as_list(trend.get("points")))
        numeric_values = [
            point["value"]
            for point in points
            if isinstance(point.get("value"), (int, float))
        ]

        vitals.append(
            {
                "field": trend.get("field"),
                "label": trend.get("label"),
                "latestValue": trend.get("latestValue"),
                "unit": trend.get("unit"),
                "latestAt": trend.get("latestAt"),
                "relation": trend.get("latestRelation"),
                "relationLabel": trend.get("latestRelationLabel"),
                "temporalBucket": trend.get("temporalBucket"),
                "trendDirection": trend.get("trendDirection"),
                "classification": trend.get("classification"),
                "observedRange": (
                    [min(numeric_values), max(numeric_values)]
                    if numeric_values
                    else []
                ),
                "points": points,
            }
        )

    package_validation = as_dict(package.get("validation"))

    declared_missing = array_of_strings(evidence.get("missingEvidence"))

    temporal_counts: dict[str, dict[str, int]] = {
        "medications": {},
        "labs": {},
        "vitals": {},
    }
    for medication in medications:
        bucket = string_value(medication.get("temporalBucket")) or "unknown"
        temporal_counts["medications"][bucket] = (
            temporal_counts["medications"].get(bucket, 0) + 1
        )
    for lab in labs:
        bucket = string_value(lab.get("temporalBucket")) or "unknown"
        temporal_counts["labs"][bucket] = temporal_counts["labs"].get(bucket, 0) + 1
    for vital in vitals:
        bucket = string_value(vital.get("temporalBucket")) or "unknown"
        temporal_counts["vitals"][bucket] = temporal_counts["vitals"].get(bucket, 0) + 1

    def closest_absolute_minutes(items: list[dict[str, Any]]) -> float | None:
        values: list[float] = []
        for item in items:
            raw = item.get("minutesFromAnchor")
            if isinstance(raw, (int, float)):
                values.append(abs(float(raw)))
            for point in as_list(item.get("points")):
                point_raw = point.get("minutesFromAnchor") if isinstance(point, dict) else None
                if isinstance(point_raw, (int, float)):
                    values.append(abs(float(point_raw)))
        return min(values) if values else None

    integrity_flags: list[dict[str, Any]] = []
    for lab in labs:
        latest_at = lab.get("latestAt")
        latest_value = lab.get("latestValue")
        same_time_values = [
            point.get("value")
            for point in as_list(lab.get("points"))
            if isinstance(point, dict) and point.get("observedAt") == latest_at
        ]
        if same_time_values and latest_value not in same_time_values:
            integrity_flags.append(
                {
                    "type": "lab_latest_value_mismatch",
                    "severity": "high",
                    "field": lab.get("field"),
                    "detail": (
                        f"latestValue={latest_value} does not match values "
                        f"{same_time_values} recorded at latestAt={latest_at}."
                    ),
                }
            )

    available_vital_fields = {
        string_value(item.get("field")).lower() for item in vitals
    }
    ambiguous_missing = [
        item
        for item in declared_missing
        if item.lower() in available_vital_fields
    ]
    if ambiguous_missing:
        integrity_flags.append(
            {
                "type": "missing_evidence_scope_ambiguity",
                "severity": "medium",
                "fields": ambiguous_missing,
                "detail": (
                    "These fields are listed as missing waveform channels while "
                    "FHIR vital observations with similar names are present. The "
                    "dashboard must label waveform-channel availability separately "
                    "from clinical-observation availability."
                ),
            }
        )

    if package_validation.get("evidenceWasTruncated") is True:
        integrity_flags.append(
            {
                "type": "evidence_truncated",
                "severity": "high",
                "detail": (
                    "The input package reports evidenceWasTruncated=true; root-cause "
                    "ranking may omit relevant context."
                ),
            }
        )

    return {
        "incidentOverview": {
            "incidentId": evidence.get("incidentId"),
            "display": incident.get("display"),
            "category": incident.get("category"),
            "severity": incident.get("severity"),
            "analysisStatus": evidence.get("analysisStatus"),
            "contextStatus": evidence.get("contextStatus"),
            "incidentStartSeconds": incident.get("incidentStartSeconds"),
            "incidentEndSeconds": incident.get("incidentEndSeconds"),
            "durationSeconds": incident.get("durationSeconds"),
            "episodeCount": incident.get("episodeCount"),
            "contextAnchor": as_dict(clinical.get("contextAnchor")),
        },
        "ecgEvidence": {
            "signalQuality": as_dict(ecg.get("signalQuality")),
            "rhythm": as_dict(ecg.get("rhythm")),
            "qrs": as_dict(ecg.get("qrs")),
            "morphology": as_dict(ecg.get("morphology")),
            "leadAgreement": as_dict(ecg.get("leadAgreement")),
            "crossEpisodeAgreement": as_dict(ecg.get("crossEpisodeAgreement")),
            "deterministicConfidence": as_dict(ecg.get("confidence")),
            "independentCandidateDetection": as_dict(
                ecg.get("independentCandidateDetection")
            ),
            "referenceAnnotations": {
                "source": dataset_reference.get("source"),
                "sourceType": dataset_reference.get("sourceType"),
                "triggerCounts": as_dict(dataset_reference.get("triggerCounts")),
                "uniqueReferenceTriggerCount": dataset_reference.get(
                    "uniqueReferenceTriggerCount"
                ),
                "isIndependentDiagnosis": dataset_reference.get(
                    "isIndependentDiagnosis"
                ),
            },
        },
        "verifiedMedicationOrders": medications,
        "verifiedLaboratoryResults": labs,
        "verifiedVitalSigns": vitals,
        "patientContext": as_dict(clinical.get("patientSummary")),
        "declaredMissingEvidence": declared_missing,
        "temporalEvidenceSummary": {
            "countsByTemporalBucket": temporal_counts,
            "closestMedicationOrderMinutesFromAnchor": closest_absolute_minutes(
                medications
            ),
            "closestLaboratoryResultMinutesFromAnchor": closest_absolute_minutes(labs),
            "closestVitalObservationMinutesFromAnchor": closest_absolute_minutes(vitals),
            "episodeNearMedicationAdministrationCount": sum(
                1
                for item in medications
                if item.get("exposureStatus") == "administered"
                and item.get("temporalBucket") == "episode_near"
            ),
            "episodeNearLaboratoryResultCount": sum(
                1
                for item in labs
                if item.get("temporalBucket") == "episode_near"
            ),
        },
        "dataIntegrityFlags": integrity_flags,
        "dataQuality": {
            **as_dict(clinical.get("dataQuality")),
            "rawWaveformsIncluded": package_validation.get("rawWaveformsIncluded"),
            "evidenceWasTruncated": package_validation.get("evidenceWasTruncated"),
            "promptCharacters": package_validation.get("promptCharacters"),
            "estimatedPromptTokens": package_validation.get(
                "estimatedPromptTokens"
            ),
        },
    }


def build_valid_reference_set(
    evidence: dict[str, Any],
    verified: dict[str, Any],
) -> set[str]:
    refs = {
        "ecg.signalQuality",
        "ecg.rhythm",
        "ecg.qrs",
        "ecg.morphology",
        "ecg.referenceAnnotations",
        "incident.timeline",
    }

    incident_id = verified["incidentOverview"].get("incidentId")
    if incident_id:
        refs.add(str(incident_id))

    for medication in verified["verifiedMedicationOrders"]:
        source_id = medication.get("sourceId")
        if source_id:
            refs.add(str(source_id))
            refs.add(f"medication:{source_id}")

    for lab in verified["verifiedLaboratoryResults"]:
        for point in lab.get("points", []):
            source_id = point.get("sourceId")
            if source_id:
                refs.add(str(source_id))
                refs.add(f"lab:{source_id}")

    for vital in verified["verifiedVitalSigns"]:
        for point in vital.get("points", []):
            source_id = point.get("sourceId")
            if source_id:
                refs.add(str(source_id))
                refs.add(f"vital:{source_id}")

    clinical = as_dict(as_dict(evidence.get("evidence")).get("clinicalContext"))
    for report in as_list(clinical.get("diagnosticReports")):
        if isinstance(report, dict) and report.get("id"):
            refs.add(str(report["id"]))
            refs.add(f"report:{report['id']}")

    return refs


# =============================================================================
# Ollama request
# =============================================================================


def build_payload(
    model: str,
    messages: list[dict[str, str]],
    context_size: int,
    max_output_tokens: int,
) -> dict[str, Any]:
    return {
        "model": model,
        "messages": messages,
        "stream": True,
        "format": MODEL_ANALYSIS_SCHEMA,
        "keep_alive": KEEP_ALIVE,
        "options": {
            "temperature": 0,
            "num_ctx": context_size,
            "num_predict": max_output_tokens,
            "top_p": 0.9,
            "repeat_penalty": 1.12,
        },
    }


def stream_ollama_response(
    model: str,
    messages: list[dict[str, str]],
    context_size: int,
    max_output_tokens: int,
    attempt: int,
) -> tuple[str, dict[str, Any]]:
    payload = build_payload(model, messages, context_size, max_output_tokens)

    loading_indicator = ActivityIndicator(
        f"Attempt {attempt}: {model} loading and processing"
    )
    loading_indicator.start()

    generation_bar: tqdm | None = None
    generated_parts: list[str] = []
    final_chunk: dict[str, Any] = {}

    try:
        with requests.post(
            OLLAMA_CHAT_URL,
            json=payload,
            stream=True,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:
            response.raise_for_status()

            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue

                try:
                    chunk = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue

                if chunk.get("error"):
                    raise RuntimeError(f"Ollama error: {chunk['error']}")

                message = chunk.get("message") or {}
                content = message.get("content") or ""

                if content:
                    if generation_bar is None:
                        loading_indicator.stop()
                        generation_bar = tqdm(
                            total=max_output_tokens,
                            desc=f"Attempt {attempt}: {model} generating",
                            unit="token",
                            dynamic_ncols=True,
                            leave=True,
                        )

                    generated_parts.append(content)
                    if generation_bar.n < max_output_tokens:
                        generation_bar.update(1)

                if chunk.get("done"):
                    final_chunk = chunk
                    break

    except requests.ConnectionError as exc:
        raise RuntimeError(
            f"Could not connect to Ollama at {OLLAMA_CHAT_URL}. "
            "Make sure Ollama is running."
        ) from exc
    except requests.Timeout as exc:
        raise RuntimeError(f"The request for {model} timed out.") from exc
    except requests.RequestException as exc:
        body = exc.response.text if exc.response is not None else ""
        raise RuntimeError(
            f"Ollama request failed for {model}: {exc}\n{body}"
        ) from exc
    finally:
        loading_indicator.stop()
        if generation_bar is not None:
            actual_tokens = final_chunk.get("eval_count")
            if isinstance(actual_tokens, int):
                generation_bar.n = min(actual_tokens, max_output_tokens)
            generation_bar.refresh()
            generation_bar.close()

    generated_text = "".join(generated_parts).strip()
    if not generated_text:
        raise RuntimeError(f"{model} returned an empty response.")

    return generated_text, final_chunk


# =============================================================================
# Relaxed parsing and clinical guardrails
# =============================================================================


def strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def extract_first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    return None


def parse_json_response(
    generated_text: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    cleaned = strip_code_fences(generated_text)
    candidates = [cleaned]
    extracted = extract_first_json_object(cleaned)
    if extracted and extracted != cleaned:
        candidates.append(extracted)

    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed, []
            return None, ["The response root must be a JSON object."]
        except json.JSONDecodeError as exc:
            last_error = exc

    return None, [f"Invalid JSON: {last_error}"]


def clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0

    if 0.0 <= number <= 1.0:
        number *= 100.0
    return round(max(0.0, min(100.0, number)), 2)


def normalize_choice(value: Any, allowed: set[str], default: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", string_value(value).lower()).strip("_")
    return normalized if normalized in allowed else default


def medication_lookup(
    verified_medications: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    by_id = {
        str(item.get("sourceId")): item
        for item in verified_medications
        if item.get("sourceId")
    }
    return by_id, verified_medications


def match_medication(
    source_id: str,
    medication_name: str,
    by_id: dict[str, dict[str, Any]],
    all_medications: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if source_id in by_id:
        return by_id[source_id]

    wanted = re.sub(r"[^a-z0-9]", "", medication_name.lower())
    if not wanted:
        return None

    for medication in all_medications:
        candidate = re.sub(
            r"[^a-z0-9]",
            "",
            string_value(medication.get("medicationName")).lower(),
        )
        if wanted in candidate or candidate in wanted:
            return medication

    return None


def normalize_model_analysis(
    response: dict[str, Any],
    verified: dict[str, Any],
    valid_references: set[str],
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []

    root_raw = as_dict(response.get("rootCauseAssessment"))
    candidates_raw = as_list(root_raw.get("candidates"))
    candidates: list[dict[str, Any]] = []

    allowed_likelihood = {"likely", "possible", "unlikely", "indeterminate"}
    allowed_strength = {
        "supported_association",
        "plausible_unconfirmed",
        "background_only",
        "not_supported",
        "indeterminate",
    }
    allowed_temporal = {
        "episode_near",
        "within_hours",
        "within_one_day",
        "historical",
        "historical_remote",
        "unknown",
    }

    for index, item in enumerate(candidates_raw, start=1):
        if not isinstance(item, dict):
            continue

        temporal_fit = normalize_choice(
            item.get("temporalFit"),
            allowed_temporal,
            "unknown",
        )
        likelihood = normalize_choice(
            item.get("likelihood"),
            allowed_likelihood,
            "indeterminate",
        )
        causal_strength = normalize_choice(
            item.get("causalStrength"),
            allowed_strength,
            "indeterminate",
        )
        confidence = clamp_confidence(item.get("confidence"))

        # Historical-only evidence cannot establish an episodic root cause.
        if temporal_fit == "historical_remote":
            confidence = min(confidence, 35.0)
            likelihood = "possible" if likelihood == "likely" else likelihood
            causal_strength = "background_only"

        source_references = [
            reference
            for reference in array_of_strings(item.get("sourceReferences"))
            if reference in valid_references
        ]
        removed_count = len(array_of_strings(item.get("sourceReferences"))) - len(
            source_references
        )
        if removed_count:
            warnings.append(
                f"Removed {removed_count} unsupported source reference(s) from "
                f"root-cause candidate {index}."
            )

        candidates.append(
            {
                "id": string_value(item.get("id")) or f"candidate-{index}",
                "category": string_value(item.get("category")) or "other",
                "title": clean_model_text(item.get("title")) or "Unspecified hypothesis",
                "hypothesis": clean_model_text(item.get("hypothesis")),
                "likelihood": likelihood,
                "causalStrength": causal_strength,
                "confidence": confidence,
                "temporalFit": temporal_fit,
                "evidenceFor": clean_model_strings(item.get("evidenceFor")),
                "evidenceAgainst": clean_model_strings(item.get("evidenceAgainst")),
                "sourceReferences": deduplicate_strings(source_references),
                "verificationNeeded": clean_model_strings(item.get("verificationNeeded")),
            }
        )

    # Sort only for dashboard display. Confidence is model confidence after guardrails,
    # not diagnostic probability.
    candidates.sort(key=lambda item: item["confidence"], reverse=True)

    # A confirmed root cause requires episode-near direct evidence. Otherwise the
    # result is explicitly hypotheses only.
    has_episode_near_supported_candidate = any(
        item["temporalFit"] == "episode_near"
        and item["causalStrength"] == "supported_association"
        and item["confidence"] >= 70
        for item in candidates
    )

    requested_conclusion = normalize_choice(
        root_raw.get("conclusion"),
        {
            "no_confirmed_root_cause",
            "possible_contributors_only",
            "likely_contributor_identified",
            "insufficient_evidence",
        },
        "no_confirmed_root_cause",
    )

    if not has_episode_near_supported_candidate:
        final_conclusion = (
            "possible_contributors_only" if candidates else "insufficient_evidence"
        )
        conclusion_confidence = min(
            clamp_confidence(root_raw.get("conclusionConfidence")),
            60.0,
        )
    else:
        final_conclusion = requested_conclusion
        conclusion_confidence = clamp_confidence(
            root_raw.get("conclusionConfidence")
        )

    primary_id = string_value(root_raw.get("primaryHypothesisId"))
    candidate_ids = {item["id"] for item in candidates}
    if primary_id not in candidate_ids:
        primary_id = candidates[0]["id"] if candidates else ""

    by_id, all_medications = medication_lookup(
        verified["verifiedMedicationOrders"]
    )
    medication_assessments: list[dict[str, Any]] = []

    for item in as_list(response.get("medicationRiskAssessment")):
        if not isinstance(item, dict):
            continue

        source_id = string_value(item.get("sourceId"))
        medication_name = string_value(item.get("medicationName"))
        verified_medication = match_medication(
            source_id,
            medication_name,
            by_id,
            all_medications,
        )

        if verified_medication is None:
            warnings.append(
                "Discarded an unsupported medication assessment because it did not "
                "match a medication resource in the input package."
            )
            continue

        evidence_against = deduplicate_strings(
            array_of_strings(item.get("evidenceAgainst"))
        )

        if verified_medication["exposureStatus"] == "ordered_only":
            evidence_against.append(
                "MedicationRequest confirms an order only; administration, adherence "
                "and exposure at the incident time are unconfirmed."
            )

        medication_assessments.append(
            {
                "sourceId": verified_medication["sourceId"],
                "medicationName": verified_medication["medicationName"],
                "resourceType": verified_medication["resourceType"],
                "orderStatus": verified_medication["orderStatus"],
                "evidenceLevel": verified_medication["evidenceLevel"],
                "exposureStatus": verified_medication["exposureStatus"],
                "dose": verified_medication["dose"],
                "eventTime": verified_medication["eventTime"],
                "minutesFromAnchor": verified_medication["minutesFromAnchor"],
                "relation": verified_medication["relation"],
                "relationLabel": verified_medication["relationLabel"],
                "temporalBucket": verified_medication["temporalBucket"],
                "possibleRelevance": clean_model_text(item.get("possibleRelevance")),
                "rationale": clean_model_text(item.get("rationale")),
                "confidence": min(
                    clamp_confidence(item.get("confidence")),
                    45.0
                    if verified_medication["exposureStatus"] == "ordered_only"
                    else 100.0,
                ),
                "evidenceFor": clean_model_strings(item.get("evidenceFor")),
                "evidenceAgainst": deduplicate_strings(evidence_against),
                "monitoringOrVerificationNeeded": clean_model_strings(
                    item.get("monitoringOrVerificationNeeded")
                ),
            }
        )

    missing_evidence: list[dict[str, str]] = []
    for item in as_list(response.get("missingEvidence")):
        if not isinstance(item, dict):
            continue
        missing_evidence.append(
            {
                "evidenceType": string_value(item.get("evidenceType")),
                "reason": clean_model_text(item.get("reason")),
                "impactOnRootCause": clean_model_text(item.get("impactOnRootCause")),
                "priority": normalize_choice(
                    item.get("priority"),
                    {"high", "medium", "low"},
                    "medium",
                ),
            }
        )

    # Preserve deterministic missing-evidence keys even when a small model omits them.
    existing_missing = {
        item["evidenceType"].strip().lower() for item in missing_evidence
    }
    for evidence_type in array_of_strings(verified.get("declaredMissingEvidence")):
        if evidence_type.lower() not in existing_missing:
            missing_evidence.append(
                {
                    "evidenceType": evidence_type,
                    "reason": "Listed as unavailable in the supplied incident package.",
                    "impactOnRootCause": (
                        "Reduces ability to correlate the ECG episode with other "
                        "physiological changes. Check whether this means a missing "
                        "waveform channel or a missing clinical observation."
                    ),
                    "priority": "medium",
                }
            )

    recommended_review: list[dict[str, Any]] = []
    prohibited_action_pattern = re.compile(
        r"\b(start|stop|discontinue|increase|decrease|change|administer|prescribe)\b",
        flags=re.IGNORECASE,
    )
    for item in as_list(response.get("recommendedClinicalReview")):
        if not isinstance(item, dict):
            continue
        action = clean_model_text(item.get("action"))
        if prohibited_action_pattern.search(action):
            warnings.append(
                "Removed a prescribing-style recommendation; this output supports "
                "review and verification only."
            )
            continue
        recommended_review.append(
            {
                "priority": normalize_choice(
                    item.get("priority"),
                    {"high", "medium", "low"},
                    "medium",
                ),
                "action": action,
                "rationale": clean_model_text(item.get("rationale")),
                "basedOnEvidence": clean_model_strings(item.get("basedOnEvidence")),
            }
        )

    dashboard_raw = as_dict(response.get("dashboardSummary"))
    dashboard_summary = {
        "headline": clean_model_text(dashboard_raw.get("headline"))
        or "Possible contributors identified; no root cause confirmed.",
        "priority": normalize_choice(
            dashboard_raw.get("priority"),
            {"critical", "high", "medium", "low", "informational"},
            "medium",
        ),
        "keyDrivers": clean_model_strings(dashboard_raw.get("keyDrivers")),
        "reviewFlags": clean_model_strings(dashboard_raw.get("reviewFlags")),
        "displayDisclaimer": clean_model_text(
            dashboard_raw.get("displayDisclaimer")
        )
        or "Hypothesis support only; not a confirmed root cause or diagnosis.",
    }

    safety_statement = clean_model_text(response.get("safetyStatement"))
    if REQUIRED_SAFETY_SENTENCE.lower() not in safety_statement.lower():
        safety_statement = (
            f"{safety_statement} {REQUIRED_SAFETY_SENTENCE}".strip()
            if safety_statement
            else REQUIRED_SAFETY_SENTENCE
        )
        warnings.append("The required safety sentence was added locally.")

    normalized = {
        "evidenceSummary": clean_model_text(response.get("evidenceSummary"))
        or "The available evidence supports hypotheses only; no root cause is confirmed.",
        "rootCauseAssessment": {
            "conclusion": final_conclusion,
            "conclusionConfidence": conclusion_confidence,
            "primaryHypothesisId": primary_id,
            "candidates": candidates,
        },
        "medicationRiskAssessment": medication_assessments,
        "contradictionsAndUncertainty": clean_model_strings(
            response.get("contradictionsAndUncertainty")
        ),
        "missingEvidence": missing_evidence,
        "recommendedClinicalReview": recommended_review,
        "dashboardSummary": dashboard_summary,
        "safetyStatement": safety_statement,
    }

    return normalized, deduplicate_strings(warnings)


def validate_analysis_structure(response: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {
        "evidenceSummary",
        "rootCauseAssessment",
        "medicationRiskAssessment",
        "contradictionsAndUncertainty",
        "missingEvidence",
        "recommendedClinicalReview",
        "dashboardSummary",
        "safetyStatement",
    }
    missing = required - set(response.keys())
    if missing:
        errors.append(f"Missing required analysis fields: {sorted(missing)}")
    if not isinstance(response.get("rootCauseAssessment"), dict):
        errors.append("rootCauseAssessment must be an object.")
    return errors


# =============================================================================
# Final response assembly
# =============================================================================


def assemble_final_response(
    verified: dict[str, Any],
    analysis: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schemaVersion": OUTPUT_SCHEMA_VERSION,
        "caseMode": CASE_MODE,
        "incidentOverview": verified["incidentOverview"],
        "patientContext": verified["patientContext"],
        "ecgEvidence": verified["ecgEvidence"],
        "verifiedMedicationOrders": verified["verifiedMedicationOrders"],
        "verifiedLaboratoryResults": verified["verifiedLaboratoryResults"],
        "verifiedVitalSigns": verified["verifiedVitalSigns"],
        "temporalEvidenceSummary": verified["temporalEvidenceSummary"],
        "dataIntegrityFlags": verified["dataIntegrityFlags"],
        "evidenceSummary": analysis["evidenceSummary"],
        "rootCauseAssessment": analysis["rootCauseAssessment"],
        "medicationRiskAssessment": analysis["medicationRiskAssessment"],
        "contradictionsAndUncertainty": analysis[
            "contradictionsAndUncertainty"
        ],
        "missingEvidence": analysis["missingEvidence"],
        "recommendedClinicalReview": analysis["recommendedClinicalReview"],
        "dashboardSummary": analysis["dashboardSummary"],
        "dataQuality": verified["dataQuality"],
        "safetyStatement": analysis["safetyStatement"],
    }


# =============================================================================
# Runtime and retry handling
# =============================================================================


def save_json(output_file: Path, data: dict[str, Any]) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def nanoseconds_to_seconds(value: Any) -> float:
    try:
        return float(value) / 1_000_000_000
    except (TypeError, ValueError):
        return 0.0


def build_runtime(
    final_chunk: dict[str, Any],
    wall_clock_seconds: float,
) -> dict[str, Any]:
    total_seconds = nanoseconds_to_seconds(final_chunk.get("total_duration"))
    load_seconds = nanoseconds_to_seconds(final_chunk.get("load_duration"))
    prompt_seconds = nanoseconds_to_seconds(final_chunk.get("prompt_eval_duration"))
    generation_seconds = nanoseconds_to_seconds(final_chunk.get("eval_duration"))
    prompt_tokens = int(final_chunk.get("prompt_eval_count") or 0)
    generated_tokens = int(final_chunk.get("eval_count") or 0)
    speed = generated_tokens / generation_seconds if generation_seconds > 0 else 0.0

    return {
        "wallClockSeconds": round(wall_clock_seconds, 2),
        "ollamaTotalSeconds": round(total_seconds, 2),
        "modelLoadSeconds": round(load_seconds, 2),
        "promptEvaluationSeconds": round(prompt_seconds, 2),
        "generationSeconds": round(generation_seconds, 2),
        "promptTokens": prompt_tokens,
        "generatedTokens": generated_tokens,
        "generationTokensPerSecond": round(speed, 2),
        "finishReason": final_chunk.get("done_reason"),
    }


def print_runtime(runtime: dict[str, Any]) -> None:
    print()
    print("=" * 80)
    print("RUNTIME")
    print("=" * 80)
    print(f"Wall-clock time:       {runtime['wallClockSeconds']:.2f} seconds")
    print(f"Ollama total time:     {runtime['ollamaTotalSeconds']:.2f} seconds")
    print(f"Model load time:       {runtime['modelLoadSeconds']:.2f} seconds")
    print(f"Prompt evaluation:     {runtime['promptEvaluationSeconds']:.2f} seconds")
    print(f"Generation time:       {runtime['generationSeconds']:.2f} seconds")
    print(f"Prompt tokens:         {runtime['promptTokens']}")
    print(f"Generated tokens:      {runtime['generatedTokens']}")
    print(
        "Generation speed:      "
        f"{runtime['generationTokensPerSecond']:.2f} tokens/second"
    )
    print(f"Finish reason:         {runtime['finishReason']}")


def calculate_next_token_limit(
    current_limit: int,
    context_size: int,
    actual_prompt_tokens: int | None,
) -> int:
    proposed = min(current_limit + TOKEN_INCREMENT, MAX_OUTPUT_TOKEN_LIMIT)
    if actual_prompt_tokens is not None:
        available = context_size - actual_prompt_tokens - 768
        proposed = min(proposed, max(512, available))
    return proposed


def estimate_prompt_tokens(package: dict[str, Any]) -> int:
    validation = as_dict(package.get("validation"))
    prompt_characters = validation.get("promptCharacters")
    estimated = validation.get("estimatedPromptTokens")

    estimates: list[int] = []
    if isinstance(prompt_characters, int) and prompt_characters > 0:
        estimates.append(int(prompt_characters / 3.0))
    if isinstance(estimated, int) and estimated > 0:
        estimates.append(int(estimated * 1.35))

    return max(estimates) if estimates else 0


def run_until_usable(
    model_key: str,
    model_name: str,
    original_messages: list[dict[str, str]],
    sanitized_evidence: dict[str, Any],
    verified: dict[str, Any],
    valid_references: set[str],
    context_size: int,
    initial_max_tokens: int,
    max_attempts: int,
) -> tuple[dict[str, Any], list[str], dict[str, Any], int, float]:
    max_tokens = initial_max_tokens
    previous_errors: list[str] = []
    started_at = time.perf_counter()

    for attempt in range(1, max_attempts + 1):
        print()
        print("=" * 80)
        print(f"RUNNING {model_key.upper()} ATTEMPT {attempt}/{max_attempts}")
        print("=" * 80)
        print(f"Model: {model_name}")
        print(f"Context size: {context_size}")
        print(f"Output token allowance: {max_tokens}")
        print()

        messages = prepare_model_messages(
            original_messages,
            sanitized_evidence,
            previous_errors if attempt > 1 else None,
        )

        generated_text, final_chunk = stream_ollama_response(
            model_name,
            messages,
            context_size,
            max_tokens,
            attempt,
        )

        parsed, parsing_errors = parse_json_response(generated_text)
        finish_reason = final_chunk.get("done_reason")

        if parsed is not None:
            normalized, warnings = normalize_model_analysis(
                parsed,
                verified,
                valid_references,
            )
            structure_errors = validate_analysis_structure(normalized)
            if not structure_errors:
                wall_clock_seconds = time.perf_counter() - started_at
                print()
                print("=" * 80)
                print("USABLE ROOT-CAUSE JSON CREATED")
                print("=" * 80)
                print(
                    "Verified measurements and medication order details were copied "
                    "from the input package, not trusted to model generation."
                )
                return (
                    normalized,
                    warnings,
                    final_chunk,
                    attempt,
                    wall_clock_seconds,
                )
            parsing_errors.extend(structure_errors)

        print()
        print("=" * 80)
        print("ATTEMPT NEEDS RETRY")
        print("=" * 80)
        for error in parsing_errors:
            print(f"- {error}")

        previous_errors = parsing_errors.copy()

        if finish_reason == "length":
            prompt_count = final_chunk.get("prompt_eval_count")
            actual_prompt_tokens = (
                prompt_count if isinstance(prompt_count, int) else None
            )
            next_limit = calculate_next_token_limit(
                max_tokens,
                context_size,
                actual_prompt_tokens,
            )
            if next_limit > max_tokens:
                max_tokens = next_limit
                print(f"Retrying with {max_tokens} maximum output tokens.")
            else:
                previous_errors.append(
                    "Return a more concise but complete JSON response because the "
                    "context window cannot provide more output space."
                )
        else:
            print(
                "The model stopped normally. The same token ceiling will be used "
                "because this was a JSON-format issue."
            )

    raise RuntimeError(
        f"The model did not return usable root-cause JSON after {max_attempts} "
        "attempt(s). No failed-response file was saved."
    )


# =============================================================================
# Main
# =============================================================================


def safe_output_key(value: str) -> str:
    cleaned = value.replace("hf.co/", "")
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", cleaned)
    return cleaned.strip("_").upper()


def main() -> None:
    args = parse_arguments()

    selected_alias = args.model
    model_name = args.model_name or AVAILABLE_MODELS[selected_alias]
    output_key = (
        selected_alias.upper()
        if args.model_name is None
        else safe_output_key(model_name)
    )

    output_file = (
        args.output.resolve()
        if args.output is not None
        else SCRIPT_DIRECTORY
        / f"SLM_Response_{output_key}_UNIFIED_ROOT_CAUSE.json"
    )

    print("=" * 80)
    print("PHASE 7 OLLAMA — UNIFIED ROOT-CAUSE BENCHMARK")
    print("=" * 80)
    print(f"Input file: {INPUT_FILE.name}")
    print(f"Selected alias: {selected_alias}")
    print(f"Model name: {model_name}")
    print(f"Context size: {args.context}")
    print(f"Initial output tokens: {args.max_tokens}")
    print(f"Output file: {output_file.name}")
    print(f"Case mode: {CASE_MODE}")
    print()

    try:
        package = load_input_package()
        messages = validate_input_messages(package)
        original_evidence = extract_evidence_json(messages)
        sanitized_evidence = sanitize_evidence_for_unified_case(
            copy.deepcopy(original_evidence)
        )
        verified = build_verified_facts(original_evidence, package)
        valid_references = build_valid_reference_set(
            original_evidence,
            verified,
        )

        conservative_prompt_estimate = estimate_prompt_tokens(package)
        if conservative_prompt_estimate:
            print(
                "Conservative prompt-token estimate: "
                f"{conservative_prompt_estimate}"
            )
            estimated_available = (
                args.context - conservative_prompt_estimate - 768
            )
            if estimated_available < args.max_tokens:
                adjusted = max(512, estimated_available)
                print(
                    f"Output token ceiling adjusted from {args.max_tokens} to "
                    f"{adjusted} to fit the context budget."
                )
                args.max_tokens = adjusted

        print(
            "Unified-case transformation is always active. The original "
            "SLM_Input_Package.json is not modified."
        )
        print(
            "Medication doses, lab values, vital values and ECG metrics in the "
            "saved response will be copied deterministically from the input package."
        )
        print(
            "Root-cause candidates remain hypotheses for review, not confirmed causes."
        )

        (
            analysis,
            warnings,
            final_chunk,
            attempts_used,
            wall_clock_seconds,
        ) = run_until_usable(
            model_key=selected_alias,
            model_name=model_name,
            original_messages=messages,
            sanitized_evidence=sanitized_evidence,
            verified=verified,
            valid_references=valid_references,
            context_size=args.context,
            initial_max_tokens=args.max_tokens,
            max_attempts=max(1, args.attempts),
        )

        final_response = assemble_final_response(
            verified,
            analysis,
            original_evidence,
        )

        runtime = build_runtime(final_chunk, wall_clock_seconds)
        print_runtime(runtime)

        saved_result = {
            "modelVariant": selected_alias,
            "model": model_name,
            "validationStatus": "passed",
            "validationMode": "relaxed_with_deterministic_fact_overlay",
            "caseMode": CASE_MODE,
            "notForClinicalUse": True,
            "attemptsUsed": attempts_used,
            "response": final_response,
            "warnings": warnings,
            "runtime": runtime,
        }

        save_json(output_file, saved_result)

        print()
        print("=" * 80)
        print("FINAL SUCCESSFUL RESPONSE")
        print("=" * 80)
        print(json.dumps(final_response, indent=2, ensure_ascii=False))

        if warnings:
            print()
            print("=" * 80)
            print("NON-BLOCKING WARNINGS")
            print("=" * 80)
            for warning in warnings:
                print(f"- {warning}")

        print()
        print(f"Successful response saved to: {output_file}")

    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print()
        print(f"Run stopped: {exc}", file=sys.stderr)
        print("No failed response file was created.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
