from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx

from app.cloud_run_auth import apply_slm_auth

from .config import (
    slm_api_key,
    slm_base_url,
    slm_chat_path,
    slm_max_output_tokens,
    slm_model,
    slm_omit_sampling_params,
    slm_reasoning_effort,
    slm_timeout_seconds,
)


class EvaluationModelError(
    RuntimeError
):
    pass


def _parse_json_object(
    content: str,
) -> dict[str, Any]:
    text = content.strip()

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
        )

    try:
        payload = json.loads(
            text
        )
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")

        if (
            start < 0
            or end <= start
        ):
            raise EvaluationModelError(
                "The model did not "
                "return a JSON object."
            )

        try:
            payload = json.loads(
                text[
                    start:end + 1
                ]
            )
        except json.JSONDecodeError as exc:
            raise EvaluationModelError(
                "The model response "
                "could not be parsed "
                "as JSON."
            ) from exc

    if not isinstance(
        payload,
        dict,
    ):
        raise EvaluationModelError(
            "The model response JSON "
            "must be an object."
        )

    return payload


async def call_model(
    *,
    messages: list[
        dict[str, str]
    ],
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
        raise EvaluationModelError(
            "SLM_MODEL is not configured."
        )

    endpoint = (
        f"{slm_base_url()}"
        f"{slm_chat_path()}"
    )

    headers = {
        "Content-Type":
            "application/json",
    }

    if slm_api_key():
        headers["Authorization"] = (
            f"Bearer {slm_api_key()}"
        )

    headers = await apply_slm_auth(
        headers,
        base_url=slm_base_url(),
    )

    request_payload = {
        "model": model_name,
        "messages": messages,
        "max_tokens":
            slm_max_output_tokens(),
        "stream": False,
        "response_format": {
            "type": "json_object",
        },
    }

    # Some current OpenAI-compatible providers (including Gemini 3.6)
    # deprecate sampling fields. Default remains unchanged for existing
    # local/Ollama-style endpoints unless explicitly enabled in env.
    if not slm_omit_sampling_params():
        request_payload["temperature"] = temperature

    reasoning_effort = slm_reasoning_effort()
    if reasoning_effort:
        request_payload["reasoning_effort"] = reasoning_effort

    # ============================================================
    # CARDINAL — PRIVATE CLOUD RUN GEMMA 4
    # ============================================================
    # The existing CARDINAL model seam remains OpenAI-compatible.
    # For the presentation provider, the local gcloud Run proxy
    # handles Cloud Run authentication and vLLM receives the
    # Gemma-specific chat template option directly.
    cardinal_provider = os.getenv(
        "CARDINAL_LLM_PROVIDER",
        "",
    ).strip().lower()

    if cardinal_provider == "gemma4":
        # Gemini-only reasoning controls must never be forwarded
        # to the vLLM Gemma endpoint.
        request_payload.pop(
            "reasoning_effort",
            None,
        )

        # The deployed vLLM endpoint was validated without the
        # provider-specific response_format extension. CARDINAL's
        # existing V7.1 prompt + parser continue to enforce JSON.
        request_payload.pop(
            "response_format",
            None,
        )

        # This matches the already-validated Cloud Run request.
        request_payload["chat_template_kwargs"] = {
            "enable_thinking": False,
        }

    started_at = time.perf_counter()

    print(
        "[KGEN EVAL SLM REQUEST]",
        {
            "provider": cardinal_provider or "default",
            "model": model_name,
            "endpoint": endpoint,
            "messageCount": len(
                messages
            ),
            "temperature": (
                None
                if slm_omit_sampling_params()
                else temperature
            ),
            "reasoningEffort": slm_reasoning_effort() or None,
            "maxOutputTokens": (
                slm_max_output_tokens()
            ),
            "timeoutSeconds": (
                slm_timeout_seconds()
            ),
        },
        flush=True,
    )

    try:
        async with (
            httpx.AsyncClient(
                timeout=httpx.Timeout(
                    slm_timeout_seconds()
                ),
            )
        ) as client:
            response = (
                await client.post(
                    endpoint,
                    json=request_payload,
                    headers=headers,
                )
            )
    except httpx.HTTPError as exc:
        raise EvaluationModelError(
            "Could not reach the SLM. "
            f"endpoint={endpoint}; "
            f"errorType="
            f"{type(exc).__name__}; "
            f"details={exc!r}"
        ) from exc

    print(
        "[KGEN EVAL SLM HTTP RESPONSE]",
        {
            "provider": cardinal_provider or "default",
            "model": model_name,
            "statusCode": (
                response.status_code
            ),
            "elapsedSeconds": round(
                time.perf_counter()
                - started_at,
                2,
            ),
        },
        flush=True,
    )

    if response.status_code >= 400:
        raise EvaluationModelError(
            "SLM request failed. "
            f"endpoint={endpoint}; "
            f"status="
            f"{response.status_code}; "
            f"body="
            f"{response.text[:1000]}"
        )

    try:
        raw = response.json()
    except ValueError as exc:
        raise EvaluationModelError(
            "SLM endpoint returned "
            "non-JSON content. "
            f"endpoint={endpoint}; "
            f"body="
            f"{response.text[:1000]}"
        ) from exc

    choices = raw.get(
        "choices",
        [],
    )

    if not choices:
        raise EvaluationModelError(
            "SLM response contained "
            "no choices. "
            f"response={raw}"
        )

    content = (
        choices[0]
        .get("message", {})
        .get("content")
    )

    if not isinstance(
        content,
        str,
    ):
        raise EvaluationModelError(
            "SLM response contained "
            "no message content."
        )

    if cardinal_provider == "gemma4":
        print(
            "[CARDINAL-SLM-RAW]",
            {
                "provider": "gemma4",
                "model": model_name,
                "content": content,
            },
            flush=True,
        )

    parsed = _parse_json_object(
        content
    )

    metadata = {
        "provider": cardinal_provider or "default",
        "name": model_name,
        "endpoint": endpoint,
        "finishReason": (
            choices[0].get(
                "finish_reason"
            )
        ),
        "usage": raw.get(
            "usage"
        ),
    }

    print(
        "[KGEN EVAL SLM COMPLETE]",
        {
            "provider": cardinal_provider or "default",
            "model": model_name,
            "elapsedSeconds": round(
                time.perf_counter()
                - started_at,
                2,
            ),
            "finishReason": (
                metadata[
                    "finishReason"
                ]
            ),
            "responseKeys": sorted(
                parsed.keys()
            ),
        },
        flush=True,
    )

    return (
        parsed,
        metadata,
    )
