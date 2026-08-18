from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import urlparse

import httpx


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode claims from an ID token already returned by Oracle's token endpoint.

    This helper is used only to discover the SMART fhirUser reference associated
    with the already-authenticated session. It does not make an authentication
    decision from an unverified JWT. The discovered Practitioner is then verified
    through the authenticated Oracle FHIR API before CARDINAL uses it as sender.
    """
    raw = str(token or "").strip()
    parts = raw.split(".")
    if len(parts) < 2:
        return {}
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    except Exception:
        return {}


def _normalize_reference(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("Practitioner/") or raw.startswith("Person/"):
        return raw
    parsed = urlparse(raw)
    path = (parsed.path or raw).rstrip("/")
    parts = [part for part in path.split("/") if part]
    for resource_type in ("Practitioner", "Person"):
        if resource_type in parts:
            idx = parts.index(resource_type)
            if idx + 1 < len(parts):
                return f"{resource_type}/{parts[idx + 1]}"
    return ""


def smart_fhir_user_reference(token_state: dict[str, Any]) -> tuple[str, str]:
    """Return (reference, source) for the authenticated SMART user."""
    for key in ("fhir_user", "fhirUser"):
        ref = _normalize_reference(token_state.get(key))
        if ref:
            return ref, f"token_state.{key}"

    claims = _decode_jwt_payload(str(token_state.get("id_token") or ""))
    for key in ("fhirUser", "fhir_user", "profile"):
        ref = _normalize_reference(claims.get(key))
        if ref:
            return ref, f"id_token.{key}"
    return "", ""


async def resolve_smart_fhir_user(token_state: dict[str, Any]) -> dict[str, Any]:
    base = str(token_state.get("fhir_base_url") or "").strip().rstrip("/")
    access_token = str(token_state.get("access_token") or "").strip()
    reference, source = smart_fhir_user_reference(token_state)
    if not reference:
        return {
            "status": "unavailable",
            "reason": "oracle_smart_fhir_user_claim_missing",
            "reference": None,
        }
    if not reference.startswith("Practitioner/"):
        return {
            "status": "unsupported",
            "reason": "oracle_communication_sender_must_be_practitioner",
            "reference": reference,
            "source": source,
        }
    if not base or not access_token:
        return {
            "status": "unavailable",
            "reason": "oracle_smart_fhir_context_incomplete",
            "reference": reference,
            "source": source,
        }

    practitioner_id = reference.split("/", 1)[1]
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{base}/Practitioner/{practitioner_id}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/fhir+json",
            },
        )

    if response.status_code >= 400:
        return {
            "status": "failed",
            "reason": "oracle_smart_fhir_user_verification_failed",
            "reference": reference,
            "source": source,
            "httpStatus": response.status_code,
            "error": response.text[:1000],
            "requestId": response.headers.get("x-request-id") or response.headers.get("opc-request-id"),
        }

    try:
        resource = response.json()
    except ValueError:
        resource = {}
    names = resource.get("name") if isinstance(resource, dict) else None
    display = ""
    if isinstance(names, list) and names and isinstance(names[0], dict):
        first = names[0]
        family = str(first.get("family") or "").strip()
        given = first.get("given") or []
        given_text = " ".join(str(v).strip() for v in given if str(v).strip()) if isinstance(given, list) else str(given or "").strip()
        display = ", ".join(v for v in (family, given_text) if v)

    return {
        "status": "ready",
        "reference": reference,
        "practitionerId": practitioner_id,
        "display": display,
        "active": resource.get("active") if isinstance(resource, dict) else None,
        "source": source,
        "httpStatus": response.status_code,
        "requestId": response.headers.get("x-request-id") or response.headers.get("opc-request-id"),
    }
