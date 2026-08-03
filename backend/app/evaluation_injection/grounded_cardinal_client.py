from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx

from app.evaluation.config import (
    slm_api_key,
    slm_base_url,
    slm_max_output_tokens,
    slm_model,
    slm_timeout_seconds,
)


class GroundedCardinalModelError(RuntimeError):
    """Raised when a grounded model request or response is invalid."""


GROUND_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "episodeSummary": {"type": "string", "minLength": 1, "maxLength": 1200},
        "detectedEpisodeContext": {"type": "string", "minLength": 1, "maxLength": 1800},
        "mostLikelyEtiology": {"type": "string", "minLength": 1, "maxLength": 1200},
        "contributingFactors": {
            "type": "array", "minItems": 1, "maxItems": 5,
            "items": {"type": "string", "minLength": 1, "maxLength": 500},
        },
        "uncertaintyAndMissingData": {
            "type": "array", "minItems": 0, "maxItems": 8,
            "items": {"type": "string", "minLength": 1, "maxLength": 500},
        },
    },
    "required": [
        "episodeSummary",
        "detectedEpisodeContext",
        "mostLikelyEtiology",
        "contributingFactors",
        "uncertaintyAndMissingData",
    ],
}


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _native_ollama_endpoint() -> str:
    base = slm_base_url().strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return f"{base}/api/chat"


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.I | re.S)
        if fenced:
            text = fenced.group(1)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                raise GroundedCardinalModelError("The model response did not contain a JSON object.")
            try:
                parsed = json.loads(text[start:end + 1])
            except json.JSONDecodeError as exc:
                raise GroundedCardinalModelError("The model response was not valid JSON.") from exc
    else:
        raise GroundedCardinalModelError("The structured response was not text or a JSON object.")
    if not isinstance(parsed, dict):
        raise GroundedCardinalModelError("The structured response must be a JSON object.")
    return parsed


def _validate_shape(payload: dict[str, Any]) -> None:
    invalid: list[str] = []
    for field in ("episodeSummary", "detectedEpisodeContext", "mostLikelyEtiology"):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            invalid.append(field)
    for field in ("contributingFactors", "uncertaintyAndMissingData"):
        value = payload.get(field)
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip()
            for item in value
        ):
            invalid.append(field)

    factors = payload.get("contributingFactors")
    if not isinstance(factors, list) or not 1 <= len(factors) <= 5:
        invalid.append("contributingFactors")

    uncertainty = payload.get("uncertaintyAndMissingData")
    if isinstance(uncertainty, list) and len(uncertainty) > 8:
        invalid.append("uncertaintyAndMissingData")
    unexpected = sorted(set(payload) - set(GROUND_RESPONSE_SCHEMA["required"]))
    if invalid or unexpected:
        raise GroundedCardinalModelError(
            "The model response did not match the five-field contract. "
            f"invalid={sorted(set(invalid))}; unexpected={unexpected}"
        )


def build_strict_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    schema_text = json.dumps(GROUND_RESPONSE_SCHEMA, ensure_ascii=False, separators=(",", ":"))
    return [
        *messages,
        {
            "role": "user",
            "content": (
                "Return JSON only. Use exactly the schema below. Write clinical content only. "
                "Never copy prompt rules, field definitions, task wording, or schema text into "
                "the response. Do not add diagnosis, treatment, recommendation, action, or "
                "next-step fields.\n" + schema_text
            ),
        },
    ]


def message_fingerprint(messages: list[dict[str, str]]) -> str:
    raw = json.dumps(messages, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def model_slug(value: str) -> str:
    return (re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-._")[:160] or "model")


def _provider() -> str:
    return os.getenv("SLM_PROVIDER", "ollama").strip().lower() or "ollama"


def _request_log(*, provider: str, model: str, endpoint: str, messages: list[dict[str, str]], label: str) -> dict[str, Any]:
    return {
        "schemaVersion": "grounded-model-request-universal-v2",
        "label": label,
        "provider": provider,
        "model": model,
        "endpoint": endpoint,
        "messages": messages,
        "promptFingerprint": message_fingerprint(messages),
        "format": GROUND_RESPONSE_SCHEMA,
        "containsAnswerKey": False,
        "modelOwnedFields": GROUND_RESPONSE_SCHEMA["required"],
        "recommendedActionsRequired": False,
    }


def _find_colab_result(root: Path, model_name: str, run_number: int, fingerprint: str) -> Path:
    expected = root / model_slug(model_name) / f"run-{run_number}" / f"{fingerprint}.json"
    if expected.exists():
        return expected
    matches: list[Path] = []
    for candidate in root.rglob(f"{fingerprint}.json"):
        if f"run-{run_number}" not in candidate.parts:
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("model") or payload.get("modelId") or "").strip() == model_name:
            matches.append(candidate)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise GroundedCardinalModelError(
            "No Colab response matched the exact prompt fingerprint. "
            f"model={model_name}; run={run_number}; fingerprint={fingerprint}; expected={expected}"
        )
    raise GroundedCardinalModelError("Multiple Colab responses matched the same model/run/fingerprint.")


def _call_colab_file(*, strict_messages: list[dict[str, str]], model_name: str, request_log_path: Path | None, request_label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    root_text = os.getenv("COLAB_RESPONSE_ROOT", "").strip()
    if not root_text:
        raise GroundedCardinalModelError("COLAB_RESPONSE_ROOT is required for SLM_PROVIDER=colab_file.")
    try:
        run_number = max(1, int(os.getenv("COLAB_RESPONSE_RUN", "1")))
    except ValueError as exc:
        raise GroundedCardinalModelError("COLAB_RESPONSE_RUN must be an integer.") from exc
    root = Path(root_text).expanduser().resolve()
    fingerprint = message_fingerprint(strict_messages)
    response_path = _find_colab_result(root, model_name, run_number, fingerprint)
    if request_log_path is not None:
        _atomic_json(request_log_path, {
            **_request_log(provider="colab_file", model=model_name, endpoint=str(response_path), messages=strict_messages, label=request_label),
            "colabRun": run_number,
        })
    try:
        payload = json.loads(response_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GroundedCardinalModelError(f"Could not read imported Colab response: {response_path}") from exc
    if payload.get("error"):
        raise GroundedCardinalModelError(f"Colab generation failed: {payload['error']}")
    parsed = _parse_json_object(payload.get("modelResponse") or payload.get("response"))
    _validate_shape(parsed)
    def number(key: str) -> float | None:
        try:
            value = payload.get(key)
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None
    metadata = {
        "provider": "google_colab_file",
        "name": model_name,
        "endpoint": str(response_path),
        "sourceFile": str(response_path),
        "colabRun": run_number,
        "promptFingerprint": fingerprint,
        "elapsedSeconds": number("latencySeconds"),
        "promptEvalCount": payload.get("inputTokens"),
        "evalCount": payload.get("outputTokens"),
        "peakGpuMemoryGiB": number("peakGpuMemoryGiB"),
        "gpuName": payload.get("gpuName"),
        "quantization": payload.get("quantization"),
        "structuredOutput": True,
        "schema": "grounded-etiology-context-universal-v1",
        "recommendedActionsRequired": False,
    }
    print("[KGEN COLAB FILE RESPONSE LOADED]", {
        "model": model_name, "run": run_number, "fingerprint": fingerprint,
        "sourceFile": str(response_path), "elapsedSeconds": metadata["elapsedSeconds"],
    }, flush=True)
    return parsed, metadata


async def _call_ollama(*, strict_messages: list[dict[str, str]], model_name: str, temperature: float, request_log_path: Path | None, request_label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    endpoint = _native_ollama_endpoint()
    headers = {"Content-Type": "application/json"}
    if slm_api_key():
        headers["Authorization"] = f"Bearer {slm_api_key()}"
    payload = {
        "model": model_name,
        "messages": strict_messages,
        "stream": False,
        "format": GROUND_RESPONSE_SCHEMA,
        "options": {"temperature": temperature, "num_predict": slm_max_output_tokens()},
        "keep_alive": "10m",
    }
    if request_log_path is not None:
        _atomic_json(request_log_path, {
            **_request_log(provider="ollama", model=model_name, endpoint=endpoint, messages=strict_messages, label=request_label),
            "options": payload["options"],
        })
    started = perf_counter()
    print("[KGEN GROUNDED SLM REQUEST]", {
        "provider": "ollama", "model": model_name, "endpoint": endpoint,
        "messageCount": len(strict_messages), "maxOutputTokens": slm_max_output_tokens(),
        "timeoutSeconds": slm_timeout_seconds(),
    }, flush=True)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(slm_timeout_seconds())) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise GroundedCardinalModelError(f"Could not reach Ollama: {exc!r}") from exc
    elapsed = round(perf_counter() - started, 2)
    if response.status_code >= 400:
        raise GroundedCardinalModelError(
            f"Ollama grounded request failed. status={response.status_code}; body={response.text[:1200]}"
        )
    try:
        raw = response.json()
    except ValueError as exc:
        raise GroundedCardinalModelError("Ollama returned non-JSON HTTP content.") from exc
    parsed = _parse_json_object((raw.get("message") or {}).get("content"))
    _validate_shape(parsed)
    metadata = {
        "provider": "ollama",
        "name": model_name,
        "endpoint": endpoint,
        "finishReason": raw.get("done_reason"),
        "promptEvalCount": raw.get("prompt_eval_count"),
        "evalCount": raw.get("eval_count"),
        "totalDuration": raw.get("total_duration"),
        "elapsedSeconds": elapsed,
        "structuredOutput": True,
        "schema": "grounded-etiology-context-universal-v1",
        "recommendedActionsRequired": False,
    }
    return parsed, metadata


async def call_grounded_cardinal_model(
    *,
    messages: list[dict[str, str]],
    model_override: str | None = None,
    temperature: float = 0.0,
    request_log_path: Path | None = None,
    request_label: str = "attempt-1",
) -> tuple[dict[str, Any], dict[str, Any]]:
    model_name = (model_override or slm_model()).strip()
    if not model_name:
        raise GroundedCardinalModelError("SLM_MODEL is not configured and no override was supplied.")
    strict_messages = build_strict_messages(messages)
    provider = _provider()
    if provider == "colab_file":
        return _call_colab_file(
            strict_messages=strict_messages, model_name=model_name,
            request_log_path=request_log_path, request_label=request_label,
        )
    if provider == "ollama":
        return await _call_ollama(
            strict_messages=strict_messages, model_name=model_name,
            temperature=temperature, request_log_path=request_log_path,
            request_label=request_label,
        )
    raise GroundedCardinalModelError(
        f"Unsupported SLM_PROVIDER={provider!r}; expected 'ollama' or 'colab_file'."
    )
