from __future__ import annotations

import html
import os
from typing import Any

import httpx

from app.escalation.levels import EscalationLevel, level_label, normalize_level, oracle_priority as native_oracle_priority
from app.escalation.models import now_iso
from app.escalation.oracle.base_urls import message_api_base


def oracle_priority(level: EscalationLevel | str) -> str:
    return native_oracle_priority(level)


def _esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _xhtml_message(*, case: dict[str, Any], open_url: str, minimal: bool = False) -> str:
    """Build a complete XHTML document suitable for Oracle's HTML->RTF converter.

    The previous implementation sent only a <div> fragment and included an <a>
    element. The sandbox accepted the recipient but rejected the send with
    'Unable to convert HTML to RTF'. This uses a complete XHTML 1.0 document,
    simple converter-friendly elements, and exposes the CARDINAL URL as text.
    """
    level = normalize_level(case.get("effectiveLevel"))
    response = case.get("modelResponse") or {}
    title = f"CARDINAL - {level_label(level)}"
    if minimal:
        paragraphs = [
            f"CARDINAL clinical response pathway: {level_label(level)}",
            f"Open CARDINAL: {open_url}",
        ]
    else:
        paragraphs = [
            f"CARDINAL clinical response pathway: {level_label(level)}",
            f"Episode Summary: {response.get('episodeSummary') or ''}",
            f"Primary Etiology: {response.get('primaryEtiology') or ''}",
            f"Reason: {case.get('modelRationale') or ''}",
            f"Correlation ID: {case.get('correlationId') or ''}",
            f"Open CARDINAL: {open_url}",
        ]
    body = "".join(f"<p>{_esc(text)}</p>" for text in paragraphs)
    return (
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" '
        '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        f"<head><title>{_esc(title)}</title></head>"
        f"<body>{body}</body></html>"
    )


def _is_html_to_rtf_error(response: httpx.Response) -> bool:
    text = (response.text or "").lower()
    return response.status_code == 400 and "unable to convert html to rtf" in text


async def _post_message(*, base: str, path: str, access_token: str, payload: dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(timeout=20.0) as client:
        return await client.post(
            f"{base}{path}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Accept-Charset": "UTF-8",
                "Content-Type": "application/json",
            },
            json=payload,
        )


async def send_group_message(
    *,
    access_token: str,
    case: dict[str, Any],
    recipient_type: str,
    recipient_id: str,
    open_url: str,
    fhir_base_url: str | None = None,
    sender_person_id: str | None = None,
) -> dict[str, Any]:
    base = message_api_base(fhir_base_url=fhir_base_url)
    if not base:
        return {"status": "skipped", "reason": "oracle_message_api_base_not_configured"}

    sender_id = (
        str(sender_person_id or "").strip()
        or os.getenv("ORACLE_ESCALATION_MESSAGE_SENDER_PERSON_ID", "").strip()
        or os.getenv("ORACLE_ESCALATION_MESSAGE_SENDER_ID", "").strip()
    )
    if not sender_id:
        return {"status": "skipped", "reason": "oracle_message_sender_person_not_configured"}

    patient_id = str(case.get("patientId") or "").strip()
    if not patient_id:
        return {"status": "skipped", "reason": "oracle_patient_id_unavailable"}

    normalized_recipient_type = str(recipient_type or "").strip().upper()
    if normalized_recipient_type not in {"GROUPINBOX", "PERSONNEL"}:
        return {
            "status": "failed",
            "reason": "oracle_group_message_recipient_type_unsupported",
            "recipientType": normalized_recipient_type,
        }

    level = normalize_level(case.get("effectiveLevel"))
    path = os.getenv("ORACLE_PATIENT_MESSAGES_PATH", "/20241001/patientMessages/sentItems").strip()
    priority = oracle_priority(level)
    payload: dict[str, Any] = {
        "messageSender": {"id": sender_id, "type": "PERSON"},
        "content": _xhtml_message(case=case, open_url=open_url),
        "subject": f"CARDINAL - {level_label(level)}",
        "patientId": patient_id,
        "priority": {"value": priority},
        "recipients": [{"type": normalized_recipient_type, "id": str(recipient_id)}],
    }
    responsible_personnel_id = os.getenv("ORACLE_ESCALATION_RESPONSIBLE_PERSONNEL_ID", "").strip()
    if responsible_personnel_id:
        payload["responsiblePersonnelId"] = responsible_personnel_id

    response_http = await _post_message(
        base=base,
        path=path,
        access_token=access_token,
        payload=payload,
    )
    content_profile = "xhtml-transitional-v1"
    retry_detail: dict[str, Any] | None = None

    # The Oracle sandbox converter can be stricter than a browser. If it rejects
    # the rich body specifically at HTML->RTF conversion, retry once with the
    # smallest complete XHTML document. The first request was not accepted, so
    # this does not duplicate a message.
    if _is_html_to_rtf_error(response_http):
        retry_detail = {
            "firstHttpStatus": response_http.status_code,
            "firstError": response_http.text[:1000],
            "firstRequestId": response_http.headers.get("opc-request-id"),
            "retriedAt": now_iso(),
        }
        payload["content"] = _xhtml_message(case=case, open_url=open_url, minimal=True)
        response_http = await _post_message(
            base=base,
            path=path,
            access_token=access_token,
            payload=payload,
        )
        content_profile = "xhtml-transitional-minimal-retry-v1"

    if response_http.status_code >= 400:
        return {
            "status": "failed",
            "httpStatus": response_http.status_code,
            "error": response_http.text[:1000],
            "priority": priority,
            "senderPersonId": sender_id,
            "recipientType": normalized_recipient_type,
            "recipientId": str(recipient_id),
            "requestId": response_http.headers.get("opc-request-id"),
            "baseUrl": base,
            "contentProfile": content_profile,
            "converterRetry": retry_detail,
        }

    try:
        body = response_http.json()
    except ValueError:
        body = {"raw": response_http.text[:1000]}

    message_id = ""
    if isinstance(body, dict):
        for key in ("id", "patientMessageId", "messageId"):
            if body.get(key):
                message_id = str(body.get(key))
                break
        if not message_id:
            items = body.get("items")
            if isinstance(items, list) and items and isinstance(items[0], dict):
                message_id = str(items[0].get("id") or items[0].get("patientMessageId") or "")

    return {
        "status": "sent",
        "httpStatus": response_http.status_code,
        "messageId": message_id or None,
        "priority": priority,
        "senderPersonId": sender_id,
        "recipientType": normalized_recipient_type,
        "recipientId": str(recipient_id),
        "response": body,
        "requestId": response_http.headers.get("opc-request-id"),
        "baseUrl": base,
        "contentProfile": content_profile,
        "converterRetry": retry_detail,
    }
