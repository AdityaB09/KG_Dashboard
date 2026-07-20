from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

import requests
from tqdm import tqdm


# -----------------------------------------------------------------------------
# Paths and Ollama settings
# -----------------------------------------------------------------------------

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
INPUT_FILE = SCRIPT_DIRECTORY / "SLM_Input_Package.json"
OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"

AVAILABLE_MODELS: dict[str, str] = {
    "medgemma27": "medgemma:27b",
    "q8": "medgemma1.5:4b-it-q8_0",
    "bf16": "medgemma1.5:4b-it-bf16",
    "biomistral": "cniongolo/biomistral",
    "biomistral_q5": "hf.co/gguf/BioMistral-7B-GGUF:Q5_K_M",
    "openbiollm_q5": "koesn/llama3-openbiollm-8b:q5_K_M",
    "openbiollm": "charlestang06/openbiollm",
    "huatuo7": "hf.co/QuantFactory/HuatuoGPT-o1-7B-GGUF:Q5_K_M",
    "huatuo8": "hf.co/QuantFactory/HuatuoGPT-o1-8B-GGUF:Q5_K_M",
    "apollo": "hf.co/FreedomIntelligence/Apollo-7B-GGUF:Q4_K_M",
    "mistral_nemo": "mistral-nemo:12b-instruct-2407-q4_K_M",
    "llama31": "llama3.1:8b",

    # Additional medical model aliases.
    # Use --model-name to override any alias with an exact Ollama model name.
    "meditron3": "hf.co/QuantFactory/Meditron3-8B-GGUF:Q4_K_M",
    "medreason8": "hf.co/SuperMaker/MedReason-8B-GGUF:Q4_K_M",
    "medical_qwen25": "hf.co/mradermacher/Medical-Qwen2.5-7B-Instruct-GGUF:Q4_K_M",
    "med_qwen2": "hf.co/mradermacher/Med-Qwen2-7B-GGUF:Q4_K_M",
    "meerkat7": "hf.co/mradermacher/meerkat-7b-v1.0-GGUF:Q4_K_M",
    "mediphi": "hf.co/surya-ravindra/MediPhi-Instruct-Q4_K_M-GGUF:Q4_K_M",
    "clinical_llama_v21": "hf.co/mradermacher/llama-3.1-8b-clinical-V2.1-GGUF:Q4_K_M",
    "clinical_llama_v20": "hf.co/mradermacher/llama-3.1-8b-clinical-V2.0-GGUF:Q4_K_M",
    "clinical_llama_v14": "hf.co/mradermacher/llama-3.1-8b-clinical-V1.4-GGUF:Q4_K_M",
    "medgemma4_q4": "hf.co/mradermacher/medgemma-4b-it-GGUF:Q4_K_M",
}

DEFAULT_CONTEXT_SIZE = 16384
DEFAULT_MAX_OUTPUT_TOKENS = 4000
MAX_OUTPUT_TOKEN_LIMIT = 7000
TOKEN_INCREMENT = 2000
REQUEST_TIMEOUT_SECONDS = 7200
KEEP_ALIVE = "10m"
MAX_ATTEMPTS = 3

REQUIRED_SAFETY_SENTENCE = (
    "This is an evidence-grounded research summary and not an independent diagnosis."
)


# -----------------------------------------------------------------------------
# Required Phase 7 response structure
# -----------------------------------------------------------------------------

REQUIRED_STRING_FIELDS = {
    "evidenceSummary",
    "safetyStatement",
}

REQUIRED_ARRAY_FIELDS = {
    "ecgFindings",
    "clinicallyRelevantContext",
    "contradictionsAndUncertainty",
    "missingEvidence",
    "suggestedClinicalReview",
}

REQUIRED_FIELDS = REQUIRED_STRING_FIELDS | REQUIRED_ARRAY_FIELDS

PHASE7_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "evidenceSummary": {"type": "string"},
        "ecgFindings": {
            "type": "array",
            "items": {"type": "string"},
        },
        "clinicallyRelevantContext": {
            "type": "array",
            "items": {"type": "string"},
        },
        "contradictionsAndUncertainty": {
            "type": "array",
            "items": {"type": "string"},
        },
        "missingEvidence": {
            "type": "array",
            "items": {"type": "string"},
        },
        "suggestedClinicalReview": {
            "type": "array",
            "items": {"type": "string"},
        },
        "safetyStatement": {"type": "string"},
    },
    "required": sorted(REQUIRED_FIELDS),
    "additionalProperties": False,
}

OUTPUT_CONTROL_MESSAGE = {
    "role": "system",
    "content": f"""
Use the entire supplied evidence package when preparing the response.

Return exactly one complete JSON object with these seven fields:
- evidenceSummary
- ecgFindings
- clinicallyRelevantContext
- contradictionsAndUncertainty
- missingEvidence
- suggestedClinicalReview
- safetyStatement

Grounding requirements:
1. Use clinically relevant evidence from the supplied package.
2. Consolidate duplicate records and avoid repeating the same sentence.
3. Distinguish deterministic ECG measurements from INCART reference annotations.
4. Distinguish episode-near FHIR context from historical or remote FHIR context.
5. Do not claim that the INCART ECG and Oracle/FHIR records are from the same real patient.
6. Do not infer that Oracle/FHIR data caused the ECG event.
7. MedicationRequest records are orders, not proof that medication was taken.
8. Dataset V annotations are reference evidence and not an independent diagnosis.
9. A zero independent morphology-candidate count does not negate V annotations.
10. Do not invent interpretations for numeric metrics when the package does not define them.
11. Return JSON only. Do not use Markdown or code fences.
12. Keep the response complete and reasonably concise.
13. Put a research/non-diagnosis disclaimer in safetyStatement. Preferred wording:
    {REQUIRED_SAFETY_SENTENCE}
""".strip(),
}


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one Ollama model against SLM_Input_Package.json."
    )

    parser.add_argument(
        "--model",
        required=True,
        choices=AVAILABLE_MODELS.keys(),
        help=(
            "Model alias. Examples: q8, biomistral_q5, openbiollm_q5, "
            "huatuo7, huatuo8, apollo, mistral_nemo, llama31, "
            "meditron3, medreason8, medical_qwen25, med_qwen2, "
            "meerkat7, mediphi, clinical_llama_v21, clinical_llama_v20, "
            "clinical_llama_v14, medgemma4_q4."
        ),
    )

    parser.add_argument(
        "--model-name",
        default=None,
        help="Exact Ollama model name. Overrides --model when supplied.",
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
        help=f"Initial maximum output tokens. Default: {DEFAULT_MAX_OUTPUT_TOKENS}",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output filename.",
    )

    parser.add_argument(
        "--attempts",
        type=int,
        default=MAX_ATTEMPTS,
        help=f"Maximum attempts. Default: {MAX_ATTEMPTS}",
    )

    return parser.parse_args()


# -----------------------------------------------------------------------------
# Progress indicator
# -----------------------------------------------------------------------------

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


# -----------------------------------------------------------------------------
# Input package
# -----------------------------------------------------------------------------

def load_input_package() -> dict[str, Any]:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Input file was not found:\n{INPUT_FILE}\n\n"
            "Place SLM_Input_Package.json in the same folder as this script."
        )

    try:
        with INPUT_FILE.open("r", encoding="utf-8") as file:
            package = json.load(file)
    except json.JSONDecodeError as exc:
        raise ValueError(f"SLM_Input_Package.json is not valid JSON: {exc}") from exc

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
            raise ValueError(f"messages[{index}].content must be a non-empty string.")

        validated.append({"role": role, "content": content})

    return validated


def build_messages(
    package_messages: list[dict[str, str]],
    previous_errors: list[str] | None = None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []

    if package_messages:
        messages.append(package_messages[0])
        messages.append(OUTPUT_CONTROL_MESSAGE)
        messages.extend(package_messages[1:])
    else:
        messages.append(OUTPUT_CONTROL_MESSAGE)

    if previous_errors:
        messages.append(
            {
                "role": "system",
                "content": (
                    "The previous output could not be parsed into the required JSON "
                    "structure. Generate the entire answer again using the same full "
                    "evidence package. Correct only these formatting/structure issues:\n"
                    + "\n".join(f"- {error}" for error in previous_errors)
                    + "\nReturn one complete JSON object only."
                ),
            }
        )

    return messages


# -----------------------------------------------------------------------------
# Ollama request
# -----------------------------------------------------------------------------

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
        "format": PHASE7_RESPONSE_SCHEMA,
        "keep_alive": KEEP_ALIVE,
        "options": {
            "temperature": 0,
            "num_ctx": context_size,
            "num_predict": max_output_tokens,
            "top_p": 0.9,
            "repeat_penalty": 1.15,
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
                    # Streaming chunks are not exact tokens. This is an activity count.
                    if generation_bar.n < max_output_tokens:
                        generation_bar.update(1)

                if chunk.get("done"):
                    final_chunk = chunk
                    break

    except requests.ConnectionError as exc:
        raise RuntimeError(
            f"Could not connect to Ollama at {OLLAMA_CHAT_URL}. Make sure Ollama is running."
        ) from exc
    except requests.Timeout as exc:
        raise RuntimeError(f"The request for {model} timed out.") from exc
    except requests.RequestException as exc:
        response_body = exc.response.text if exc.response is not None else ""
        raise RuntimeError(
            f"Ollama request failed for {model}: {exc}\n{response_body}"
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


# -----------------------------------------------------------------------------
# Relaxed parsing, normalization, and validation
# -----------------------------------------------------------------------------

def strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def extract_first_json_object(text: str) -> str | None:
    """Extract the first balanced JSON object while respecting quoted strings."""
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
        result: list[str] = []
        for item in value:
            text = string_value(item)
            if text:
                result.append(text)
        return result

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


def normalize_response(response: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """
    Normalize minor model mistakes instead of consuming another expensive attempt.

    Safety wording is repaired locally. It is not used as a retry condition.
    """
    warnings: list[str] = []

    normalized: dict[str, Any] = {
        "evidenceSummary": string_value(response.get("evidenceSummary")),
        "ecgFindings": deduplicate_strings(
            array_of_strings(response.get("ecgFindings"))
        ),
        "clinicallyRelevantContext": deduplicate_strings(
            array_of_strings(response.get("clinicallyRelevantContext"))
        ),
        "contradictionsAndUncertainty": deduplicate_strings(
            array_of_strings(response.get("contradictionsAndUncertainty"))
        ),
        "missingEvidence": deduplicate_strings(
            array_of_strings(response.get("missingEvidence"))
        ),
        "suggestedClinicalReview": deduplicate_strings(
            array_of_strings(response.get("suggestedClinicalReview"))
        ),
        "safetyStatement": string_value(response.get("safetyStatement")),
    }

    if not normalized["evidenceSummary"]:
        normalized["evidenceSummary"] = (
            "The model returned structured evidence sections, but did not provide "
            "a separate evidence summary."
        )
        warnings.append("evidenceSummary was empty and was filled locally.")

    safety_lower = normalized["safetyStatement"].lower()
    has_non_diagnosis = any(
        phrase in safety_lower
        for phrase in (
            "not an independent diagnosis",
            "not a diagnosis",
            "does not constitute a diagnosis",
            "should not be considered a diagnosis",
            "not intended as a diagnosis",
        )
    )

    if not has_non_diagnosis:
        existing = normalized["safetyStatement"].strip()
        normalized["safetyStatement"] = (
            f"{existing} {REQUIRED_SAFETY_SENTENCE}".strip()
            if existing
            else REQUIRED_SAFETY_SENTENCE
        )
        warnings.append(
            "The research/non-diagnosis disclaimer was added locally instead of retrying."
        )

    # These are warnings only. They do not trigger another model attempt.
    combined = json.dumps(normalized, ensure_ascii=False).lower()
    warning_phrases = {
        "the patient is currently taking": (
            "Possible unsupported medication-adherence wording detected."
        ),
        "the patient is taking": (
            "Possible unsupported medication-adherence wording detected."
        ),
        "caused the ventricular ectopy": (
            "Possible unsupported causal wording detected."
        ),
        "contributed to the ventricular ectopy": (
            "Possible unsupported causal wording detected."
        ),
        "same patient": (
            "Review same-patient wording; the package describes a controlled pairing."
        ),
    }

    for phrase, warning in warning_phrases.items():
        if phrase in combined:
            warnings.append(warning)

    return normalized, deduplicate_strings(warnings)


def validate_required_structure(response: dict[str, Any]) -> list[str]:
    """Only failures that make the JSON unusable trigger another attempt."""
    errors: list[str] = []

    for field in REQUIRED_STRING_FIELDS:
        if not isinstance(response.get(field), str):
            errors.append(f"{field} must be a string.")

    for field in REQUIRED_ARRAY_FIELDS:
        value = response.get(field)
        if not isinstance(value, list):
            errors.append(f"{field} must be an array.")
        elif not all(isinstance(item, str) for item in value):
            errors.append(f"Every item in {field} must be a string.")

    return errors


# -----------------------------------------------------------------------------
# Saving and runtime
# -----------------------------------------------------------------------------

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
        f"Generation speed:      "
        f"{runtime['generationTokensPerSecond']:.2f} tokens/second"
    )
    print(f"Finish reason:         {runtime['finishReason']}")


# -----------------------------------------------------------------------------
# Retry logic
# -----------------------------------------------------------------------------

def calculate_next_token_limit(
    current_limit: int,
    context_size: int,
    actual_prompt_tokens: int | None,
) -> int:
    proposed = min(current_limit + TOKEN_INCREMENT, MAX_OUTPUT_TOKEN_LIMIT)

    if actual_prompt_tokens is not None:
        available = context_size - actual_prompt_tokens - 512
        proposed = min(proposed, max(256, available))

    return proposed


def run_until_usable(
    model_key: str,
    model_name: str,
    package_messages: list[dict[str, str]],
    context_size: int,
    initial_max_tokens: int,
    max_attempts: int,
) -> tuple[dict[str, Any], list[str], dict[str, Any], int, float]:
    max_tokens = initial_max_tokens
    previous_errors: list[str] = []
    total_started_at = time.perf_counter()

    for attempt in range(1, max_attempts + 1):
        print()
        print("=" * 80)
        print(f"RUNNING {model_key.upper()} ATTEMPT {attempt}/{max_attempts}")
        print("=" * 80)
        print(f"Model: {model_name}")
        print(f"Context size: {context_size}")
        print(f"Output token allowance: {max_tokens}")
        print()

        messages = build_messages(
            package_messages,
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
            normalized, warnings = normalize_response(parsed)
            structure_errors = validate_required_structure(normalized)

            if not structure_errors:
                wall_clock_seconds = time.perf_counter() - total_started_at

                print()
                print("=" * 80)
                print("USABLE JSON CREATED")
                print("=" * 80)
                print(
                    "Safety wording and minor field issues are normalized locally; "
                    "they do not trigger another expensive model attempt."
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

        # Increase output allowance only if Ollama actually hit the limit.
        if finish_reason == "length":
            prompt_count = final_chunk.get("prompt_eval_count")
            actual_prompt_tokens = prompt_count if isinstance(prompt_count, int) else None
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
                    "The response must be shorter because the context window cannot "
                    "provide additional output space."
                )
        else:
            print(
                "The model stopped normally. The same output allowance will be used "
                "for the retry because this was a JSON-format issue, not a token-limit issue."
            )

    raise RuntimeError(
        f"The model did not return parseable required JSON after {max_attempts} attempts. "
        "No failed-response file was created."
    )


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def safe_output_key(value: str) -> str:
    cleaned = value.replace("hf.co/", "")
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", cleaned)
    return cleaned.strip("_").upper()


def main() -> None:
    args = parse_arguments()

    selected_alias = args.model
    model_name = args.model_name or AVAILABLE_MODELS[selected_alias]
    output_key = selected_alias.upper() if args.model_name is None else safe_output_key(model_name)

    output_file = (
        args.output.resolve()
        if args.output is not None
        else SCRIPT_DIRECTORY / f"SLM_Response_{output_key}.json"
    )

    print("=" * 80)
    print("PHASE 7 OLLAMA INFERENCE — RELAXED VALIDATION")
    print("=" * 80)
    print(f"Input file: {INPUT_FILE.name}")
    print(f"Selected alias: {selected_alias}")
    print(f"Model name: {model_name}")
    print(f"Context size: {args.context}")
    print(f"Initial output tokens: {args.max_tokens}")
    print(f"Output file: {output_file.name}")
    print()

    try:
        package = load_input_package()
        package_messages = validate_input_messages(package)

        package_validation = package.get("validation", {})
        estimated_prompt_tokens = (
            package_validation.get("estimatedPromptTokens")
            if isinstance(package_validation, dict)
            else None
        )

        if estimated_prompt_tokens is not None:
            print(f"Package-estimated prompt tokens: {estimated_prompt_tokens}")

        print("The complete package messages will be sent to the model.")

        (
            model_response,
            warnings,
            final_chunk,
            attempts_used,
            wall_clock_seconds,
        ) = run_until_usable(
            model_key=selected_alias,
            model_name=model_name,
            package_messages=package_messages,
            context_size=args.context,
            initial_max_tokens=args.max_tokens,
            max_attempts=max(1, args.attempts),
        )

        runtime = build_runtime(final_chunk, wall_clock_seconds)
        print_runtime(runtime)

        saved_result = {
            "modelVariant": selected_alias,
            "model": model_name,
            "validationStatus": "passed",
            "validationMode": "relaxed",
            "attemptsUsed": attempts_used,
            "response": model_response,
            "warnings": warnings,
            "runtime": runtime,
        }

        save_json(output_file, saved_result)

        print()
        print("=" * 80)
        print("FINAL SUCCESSFUL RESPONSE")
        print("=" * 80)
        print(json.dumps(model_response, indent=2, ensure_ascii=False))

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