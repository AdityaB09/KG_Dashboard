from __future__ import annotations

import json
import re
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


class CardinalSchemaModelError(RuntimeError):
    pass


CARDINAL_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "episodeSummary": {
            "type": "string",
            "minLength": 1,
        },
        "rhythmInterpretation": {
            "type": "string",
            "minLength": 1,
        },
        "clinicalContext": {
            "type": "string",
            "minLength": 1,
        },
        "mostLikelyEtiology": {
            "type": "string",
            "minLength": 1,
        },
        "contributingFactors": {
            "type": "array",
            "minItems": 1,
            "maxItems": 12,
            "items": {
                "type": "string",
                "minLength": 1,
            },
        },
        "recommendedImmediateActions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 12,
            "items": {
                "type": "string",
                "minLength": 1,
            },
        },
        "uncertaintyAndMissingData": {
            "type": "array",
            "minItems": 1,
            "maxItems": 12,
            "items": {
                "type": "string",
                "minLength": 1,
            },
        },
    },
    "required": [
        "episodeSummary",
        "rhythmInterpretation",
        "clinicalContext",
        "mostLikelyEtiology",
        "contributingFactors",
        "recommendedImmediateActions",
        "uncertaintyAndMissingData",
    ],
}


def _native_ollama_endpoint() -> str:
    base = slm_base_url().strip().rstrip("/")

    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")

    return f"{base}/api/chat"


def _parse_json_object(
    value: Any,
) -> dict[str, Any]:
    if isinstance(value, dict):
        return value

    if not isinstance(value, str):
        raise CardinalSchemaModelError(
            "The structured model response was not text or a JSON object."
        )

    text = value.strip()

    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\s*```$",
            "",
            text,
        ).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CardinalSchemaModelError(
            "The structured model response was not valid JSON."
        ) from exc

    if not isinstance(parsed, dict):
        raise CardinalSchemaModelError(
            "The structured model response must be a JSON object."
        )

    return parsed


def _validate_required_shape(
    payload: dict[str, Any],
) -> None:
    required_strings = (
        "episodeSummary",
        "rhythmInterpretation",
        "clinicalContext",
        "mostLikelyEtiology",
    )

    required_lists = (
        "contributingFactors",
        "recommendedImmediateActions",
        "uncertaintyAndMissingData",
    )

    missing: list[str] = []

    for key in required_strings:
        value = payload.get(key)

        if not isinstance(value, str) or not value.strip():
            missing.append(key)

    for key in required_lists:
        value = payload.get(key)

        if (
            not isinstance(value, list)
            or not value
            or not all(
                isinstance(item, str)
                and item.strip()
                for item in value
            )
        ):
            missing.append(key)

    unexpected = sorted(
        set(payload)
        - set(
            CARDINAL_RESPONSE_SCHEMA[
                "required"
            ]
        )
    )

    if missing or unexpected:
        raise CardinalSchemaModelError(
            "The structured model response did not match the CARDINAL "
            f"contract. missing_or_empty={missing}; "
            f"unexpected={unexpected}"
        )


async def call_cardinal_schema_model(
    *,
    messages: list[dict[str, str]],
    model_override: str | None = None,
    temperature: float = 0.0,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
]:
    model_name = (
        model_override
        or slm_model()
    ).strip()

    if not model_name:
        raise CardinalSchemaModelError(
            "SLM_MODEL is not configured."
        )

    endpoint = _native_ollama_endpoint()

    headers = {
        "Content-Type": "application/json",
    }

    if slm_api_key():
        headers["Authorization"] = (
            f"Bearer {slm_api_key()}"
        )

    schema_text = json.dumps(
        CARDINAL_RESPONSE_SCHEMA,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    strict_messages = [
        *messages,
        {
            "role": "user",
            "content": (
                "The response must satisfy this exact JSON Schema. "
                "Populate every required field. Return JSON only.\n"
                f"{schema_text}"
            ),
        },
    ]

    request_payload = {
        "model": model_name,
        "messages": strict_messages,
        "stream": False,
        "format": CARDINAL_RESPONSE_SCHEMA,
        "options": {
            "temperature": temperature,
            "num_predict": slm_max_output_tokens(),
        },
        "keep_alive": "10m",
    }

    started_at = perf_counter()

    print(
        "[KGEN CARDINAL SCHEMA REQUEST]",
        {
            "model": model_name,
            "endpoint": endpoint,
            "messageCount": len(strict_messages),
            "maxOutputTokens": slm_max_output_tokens(),
            "timeoutSeconds": slm_timeout_seconds(),
            "schemaRequiredKeys": (
                CARDINAL_RESPONSE_SCHEMA[
                    "required"
                ]
            ),
        },
        flush=True,
    )

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                slm_timeout_seconds()
            ),
        ) as client:
            response = await client.post(
                endpoint,
                json=request_payload,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise CardinalSchemaModelError(
            "Could not reach Ollama's native structured-output endpoint. "
            f"endpoint={endpoint}; error={exc!r}"
        ) from exc

    elapsed_seconds = round(
        perf_counter() - started_at,
        2,
    )

    if response.status_code >= 400:
        raise CardinalSchemaModelError(
            "Ollama structured-output request failed. "
            f"endpoint={endpoint}; status={response.status_code}; "
            f"body={response.text[:1200]}"
        )

    try:
        raw = response.json()
    except ValueError as exc:
        raise CardinalSchemaModelError(
            "Ollama returned non-JSON HTTP content."
        ) from exc

    content = (
        raw.get("message", {})
        .get("content")
    )

    parsed = _parse_json_object(
        content
    )

    _validate_required_shape(
        parsed
    )

    metadata = {
        "name": model_name,
        "endpoint": endpoint,
        "finishReason": raw.get(
            "done_reason"
        ),
        "promptEvalCount": raw.get(
            "prompt_eval_count"
        ),
        "evalCount": raw.get(
            "eval_count"
        ),
        "totalDuration": raw.get(
            "total_duration"
        ),
        "elapsedSeconds": (
            elapsed_seconds
        ),
        "structuredOutput": True,
        "schema": (
            "cardinal-response-v1"
        ),
    }

    print(
        "[KGEN CARDINAL SCHEMA COMPLETE]",
        {
            "model": model_name,
            "elapsedSeconds": (
                elapsed_seconds
            ),
            "responseKeys": sorted(
                parsed.keys()
            ),
            "finishReason": (
                metadata[
                    "finishReason"
                ]
            ),
        },
        flush=True,
    )

    return parsed, metadata
