from __future__ import annotations

import json
import re
import time
from typing import Any, Mapping

import httpx

from app.cloud_run_auth import apply_slm_auth

from app.phase7.config import (
    phase7_settings,
)


JSON_OUTPUT_CONTROL = {
    "role": "system",
    "content": (
        "Return exactly one valid JSON object. "
        "Do not use Markdown, code fences, headings, "
        "or text outside the JSON object. Preserve the "
        "response structure requested by the supplied prompt."
    ),
}


def _endpoint() -> str:
    base = (
        phase7_settings
        .slm_base_url
        .rstrip("/")
    )

    path = (
        phase7_settings
        .slm_chat_path
    )

    if not path.startswith("/"):
        path = f"/{path}"

    return f"{base}{path}"


def _parse_json_object(
    value: Any,
) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value

    if not isinstance(value, str):
        return None

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
        )

    try:
        parsed = json.loads(text)
        return (
            parsed
            if isinstance(parsed, dict)
            else None
        )
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")

        if start < 0 or end <= start:
            return None

        try:
            parsed = json.loads(
                text[start:end + 1]
            )
            return (
                parsed
                if isinstance(parsed, dict)
                else None
            )
        except json.JSONDecodeError:
            return None


async def _post_model(
    *,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    async with httpx.AsyncClient(
        timeout=(
            phase7_settings
            .slm_timeout_seconds
        )
    ) as client:
        response = await client.post(
            _endpoint(),
            headers=headers,
            json=payload,
        )

        response.raise_for_status()
        return response.json()


async def run_slm(
    prompt_package: Mapping[
        str,
        Any,
    ],
) -> dict[str, Any]:
    if not phase7_settings.slm_enabled:
        return {
            "status": "disabled",
            "message": (
                "SLM_ENABLED is false."
            ),
        }

    if not (
        phase7_settings.slm_base_url
        and phase7_settings.slm_model
    ):
        return {
            "status": (
                "configuration_missing"
            ),
            "message": (
                "SLM_BASE_URL and SLM_MODEL "
                "must be configured."
            ),
        }

    headers = {
        "Content-Type": (
            "application/json"
        ),
    }

    if phase7_settings.slm_api_key:
        headers["Authorization"] = (
            "Bearer "
            f"{phase7_settings.slm_api_key}"
        )

    headers = await apply_slm_auth(
        headers,
        base_url=phase7_settings.slm_base_url,
    )

    source_messages = list(
        prompt_package.get(
            "messages"
        )
        or []
    )

    payload = {
        "model": (
            phase7_settings.slm_model
        ),
        "messages": [
            JSON_OUTPUT_CONTROL,
            *source_messages,
        ],
        "temperature": 0.0,
        "max_tokens": (
            phase7_settings
            .slm_max_output_tokens
        ),
        "stream": False,
        "response_format": {
            "type": "json_object",
        },
    }

    started_at = time.perf_counter()

    print(
        "[KGEN PHASE7 SLM REQUEST]",
        {
            "model": (
                phase7_settings
                .slm_model
            ),
            "endpoint": _endpoint(),
            "messageCount": len(
                payload["messages"]
            ),
            "maxTokens": (
                phase7_settings
                .slm_max_output_tokens
            ),
            "jsonMode": True,
        },
        flush=True,
    )

    try:
        try:
            body = await _post_model(
                payload=payload,
                headers=headers,
            )
        except httpx.HTTPStatusError as error:
            if (
                error.response.status_code
                not in {
                    400,
                    404,
                    422,
                }
            ):
                raise

            print(
                "[KGEN PHASE7 SLM JSON MODE FALLBACK]",
                {
                    "statusCode": (
                        error.response
                        .status_code
                    ),
                },
                flush=True,
            )

            fallback = dict(payload)
            fallback.pop(
                "response_format",
                None,
            )

            body = await _post_model(
                payload=fallback,
                headers=headers,
            )

    except Exception as error:
        print(
            "[KGEN PHASE7 SLM FAILED]",
            {
                "errorType": (
                    type(error).__name__
                ),
                "message": str(error),
            },
            flush=True,
        )

        return {
            "status": "failed",
            "errorType": (
                type(error).__name__
            ),
            "message": str(error),
        }

    choices = body.get(
        "choices"
    ) or []

    content = None

    if choices:
        content = (
            choices[0]
            .get("message", {})
            .get("content")
        )

    parsed = _parse_json_object(
        content
    )

    if (
        parsed is None
        and isinstance(content, str)
        and content.strip()
    ):
        print(
            "[KGEN PHASE7 SLM JSON REPAIR REQUEST]",
            {
                "originalCharacters": len(
                    content
                ),
            },
            flush=True,
        )

        repair_payload = {
            "model": (
                phase7_settings
                .slm_model
            ),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Convert the supplied output into "
                        "exactly one valid JSON object. "
                        "Preserve supported facts and return "
                        "JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": content,
                },
            ],
            "temperature": 0.0,
            "max_tokens": (
                phase7_settings
                .slm_max_output_tokens
            ),
            "stream": False,
            "response_format": {
                "type": "json_object",
            },
        }

        try:
            repair_body = await _post_model(
                payload=repair_payload,
                headers=headers,
            )

            repair_choices = (
                repair_body.get(
                    "choices"
                )
                or []
            )

            repair_content = (
                repair_choices[0]
                .get("message", {})
                .get("content")
                if repair_choices
                else None
            )

            repaired = _parse_json_object(
                repair_content
            )

            if repaired is not None:
                parsed = repaired
                body = repair_body

                print(
                    "[KGEN PHASE7 SLM JSON REPAIR COMPLETE]",
                    {
                        "responseKeys": sorted(
                            repaired.keys()
                        ),
                    },
                    flush=True,
                )

        except Exception as repair_error:
            print(
                "[KGEN PHASE7 SLM JSON REPAIR FAILED]",
                {
                    "errorType": (
                        type(
                            repair_error
                        ).__name__
                    ),
                    "message": str(
                        repair_error
                    ),
                },
                flush=True,
            )

    if parsed is None:
        return {
            "status": "failed",
            "errorType": (
                "InvalidModelJson"
            ),
            "message": (
                "The selected model did not "
                "return a valid JSON object."
            ),
            "model": (
                phase7_settings.slm_model
            ),
            "content": content,
            "providerResponse": body,
        }

    canonical_content = json.dumps(
        parsed,
        ensure_ascii=False,
        separators=(
            ",",
            ":",
        ),
    )

    print(
        "[KGEN PHASE7 SLM COMPLETE]",
        {
            "model": (
                phase7_settings
                .slm_model
            ),
            "elapsedSeconds": round(
                time.perf_counter()
                - started_at,
                2,
            ),
            "hasContent": True,
            "jsonValidated": True,
            "responseKeys": sorted(
                parsed.keys()
            ),
        },
        flush=True,
    )

    return {
        "status": "ready",
        "model": (
            phase7_settings.slm_model
        ),
        "content": canonical_content,
        "parsedContent": parsed,
        "jsonValidated": True,
        "providerResponse": body,
    }
