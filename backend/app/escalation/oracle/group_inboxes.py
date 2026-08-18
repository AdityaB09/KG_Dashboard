from __future__ import annotations

import os
from typing import Any

import httpx

from app.escalation.oracle.base_urls import recipient_api_base


class OracleGroupInboxError(RuntimeError):
    pass


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }


async def discover_group_inboxes(
    *,
    access_token: str,
    fhir_base_url: str | None = None,
    name: str | None = None,
    inbox_id: str | None = None,
) -> dict[str, Any]:
    base = recipient_api_base(fhir_base_url=fhir_base_url)
    if not base:
        return {
            "status": "skipped",
            "reason": "oracle_recipient_api_base_not_configured",
            "items": [],
        }
    path = os.getenv("ORACLE_GROUP_INBOXES_PATH", "/20241001/groupInboxes").strip()
    params: dict[str, str] = {}
    if str(name or "").strip():
        params["name"] = str(name).strip()
    elif str(inbox_id or "").strip():
        params["id"] = str(inbox_id).strip()

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{base}{path}",
            headers=_headers(access_token),
            params=params or None,
        )
    if response.status_code >= 400:
        return {
            "status": "failed",
            "httpStatus": response.status_code,
            "error": response.text[:1000],
            "items": [],
            "requestId": response.headers.get("opc-request-id"),
            "baseUrl": base,
        }
    payload = response.json()
    items = payload.get("items") if isinstance(payload, dict) else payload
    return {
        "status": "ready",
        "httpStatus": response.status_code,
        "items": items if isinstance(items, list) else [],
        "requestId": response.headers.get("opc-request-id"),
        "baseUrl": base,
    }


def _iter_objects(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _iter_objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_objects(item)


def find_group_inbox(payload: Any, *, name: str) -> dict[str, Any] | None:
    target = str(name or "").strip().casefold()
    if not target:
        return None
    for item in _iter_objects(payload):
        candidates = [
            item.get("name"),
            item.get("display"),
            item.get("displayName"),
            item.get("description"),
        ]
        if any(str(value or "").strip().casefold() == target for value in candidates):
            return item
    return None
