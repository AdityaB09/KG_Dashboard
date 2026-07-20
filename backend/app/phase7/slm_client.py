from __future__ import annotations

from typing import Any, Mapping

import httpx

from app.phase7.config import (
    phase7_settings,
)


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

    payload = {
        "model": (
            phase7_settings.slm_model
        ),
        "messages": (
            prompt_package.get(
                "messages"
            )
            or []
        ),
        "temperature": 0.0,
        "max_tokens": (
            phase7_settings
            .slm_max_output_tokens
        ),
    }

    try:
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

            body = response.json()

    except Exception as error:
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
        message = (
            choices[0].get(
                "message"
            )
            or {}
        )

        content = message.get(
            "content"
        )

    return {
        "status": "ready",
        "model": (
            phase7_settings.slm_model
        ),
        "content": content,
        "providerResponse": body,
    }
