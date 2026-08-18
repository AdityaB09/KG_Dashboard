from __future__ import annotations

import os
from typing import Any

import httpx

from app.escalation.oracle.base_urls import message_api_base


async def validate_recipient(
    *,
    access_token: str,
    recipient_type: str,
    recipient_id: str,
    patient_id: str = "",
    fhir_base_url: str | None = None,
) -> dict[str, Any]:
    base = message_api_base(fhir_base_url=fhir_base_url)
    if not base:
        return {"status": "skipped", "reason": "oracle_message_api_base_not_configured"}

    normalized_type = str(recipient_type or "").strip().upper()
    if normalized_type not in {"GROUPINBOX", "PERSONNEL"}:
        return {
            "status": "failed",
            "reason": "oracle_recipient_type_not_validatable",
            "recipientType": normalized_type,
        }

    path = os.getenv(
        "ORACLE_VALIDATE_RECIPIENTS_PATH",
        "/20241001/actions/validateRecipients",
    ).strip()
    payload: dict[str, Any] = {
        "category": "MESSAGES",
        "recipients": [{"recipientType": normalized_type, "id": str(recipient_id)}],
    }
    if patient_id:
        payload["patientId"] = str(patient_id)

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            f"{base}{path}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if response.status_code >= 400:
        return {
            "status": "failed",
            "httpStatus": response.status_code,
            "error": response.text[:1000],
            "recipientType": normalized_type,
            "recipientId": str(recipient_id),
            "requestId": response.headers.get("opc-request-id"),
            "baseUrl": base,
        }

    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:1000]}

    items = body.get("items") if isinstance(body, dict) else None
    matched: dict[str, Any] | None = None
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("id") or "") == str(recipient_id):
                matched = item
                break
        if matched is None and len(items) == 1 and isinstance(items[0], dict):
            matched = items[0]

    is_valid = None if matched is None else bool(matched.get("isValid"))
    return {
        "status": "validated" if is_valid is not False else "invalid",
        "isValid": is_valid,
        "httpStatus": response.status_code,
        "recipientType": normalized_type,
        "recipientId": str(recipient_id),
        "response": body,
        "requestId": response.headers.get("opc-request-id"),
        "baseUrl": base,
    }
