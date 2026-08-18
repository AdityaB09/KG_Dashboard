from __future__ import annotations

import os
from typing import Any

import httpx

from app.escalation.oracle.base_urls import personnel_api_base


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }


async def discover_personnel(
    *,
    access_token: str,
    fhir_base_url: str | None = None,
    free_text_name: str | None = None,
) -> dict[str, Any]:
    base = personnel_api_base(fhir_base_url=fhir_base_url)
    if not base:
        return {"status": "skipped", "reason": "oracle_personnel_api_base_not_configured", "items": []}
    path = os.getenv("ORACLE_PERSONNEL_PATH", "/20240101/personnel").strip()
    params: dict[str, str] = {"limit": "1000"}
    if str(free_text_name or "").strip():
        params["freeTextName"] = str(free_text_name).strip()
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{base}{path}",
            headers=_headers(access_token),
            params=params,
        )
    if response.status_code >= 400:
        return {
            "status": "failed",
            "httpStatus": response.status_code,
            "error": response.text[:1000],
            "items": [],
        }
    try:
        body = response.json()
    except ValueError:
        body = {}
    items = body.get("items") if isinstance(body, dict) else []
    return {
        "status": "ready",
        "httpStatus": response.status_code,
        "items": items if isinstance(items, list) else [],
        "requestId": response.headers.get("opc-request-id"),
    }
