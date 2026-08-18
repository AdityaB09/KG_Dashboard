from __future__ import annotations

import base64
import os
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from app.escalation.levels import EscalationLevel, level_label, normalize_level
from app.escalation.models import now_iso
from app.escalation.oracle.fhir_user import resolve_smart_fhir_user


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _extract_resource_id(resource: dict[str, Any], location: str | None) -> str:
    resource_id = str(resource.get("id") or "").strip()
    if resource_id:
        return resource_id
    if not location:
        return ""
    path = urlparse(location).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else ""


async def _resolve_smart_practitioner(
    token_state: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    identity = await resolve_smart_fhir_user(token_state)
    if identity.get("status") == "ready":
        reference = str(identity.get("reference") or "").strip()
        if reference.startswith("Practitioner/"):
            return reference, identity

    return "", {
        **identity,
        "nextStep": (
            "Use a fresh Oracle Provider SMART login with openid/fhirUser and "
            "user/Practitioner.rs."
        ),
    }


def _content(case: dict[str, Any], level: EscalationLevel) -> str:
    response = case.get("modelResponse") or {}
    return "\n".join(
        [
            f"CARDINAL - {level_label(level)}",
            f"Escalation: {level.value}",
            f"Episode Summary: {response.get('episodeSummary') or ''}",
            f"Primary Etiology: {response.get('primaryEtiology') or ''}",
            f"Escalation Rationale: {case.get('modelRationale') or ''}",
            f"Correlation ID: {case.get('correlationId') or ''}",
        ]
    )


def _build_payload(
    *,
    case: dict[str, Any],
    level: EscalationLevel,
    practitioner_reference: str,
) -> dict[str, Any]:
    encoded = base64.b64encode(_content(case, level).encode("utf-8")).decode("ascii")

    return {
        "resourceType": "Communication",
        "status": "completed",
        "category": [
            {
                "coding": [
                    {
                        "system": (
                            "http://terminology.hl7.org/CodeSystem/"
                            "communication-category"
                        ),
                        "code": "notification",
                    }
                ]
            }
        ],
        "recipient": [{"reference": practitioner_reference}],
        "sender": {"reference": practitioner_reference},
        "payload": [
            {
                "contentAttachment": {
                    "contentType": "text/plain",
                    "data": encoded,
                }
            }
        ],
    }


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/fhir+json",
        "Content-Type": "application/fhir+json",
    }


async def _verify_created(
    *,
    base: str,
    access_token: str,
    resource_id: str,
) -> dict[str, Any]:
    if not resource_id:
        return {"status": "skipped", "reason": "communication_id_unavailable"}

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{base}/Communication/{quote(resource_id, safe='')}",
            headers=_headers(access_token),
        )

    return {
        "status": "verified" if response.status_code < 400 else "failed",
        "httpStatus": response.status_code,
        "verifiedAt": now_iso(),
        "requestId": (
            response.headers.get("x-request-id")
            or response.headers.get("opc-request-id")
        ),
        "error": response.text[:1000] if response.status_code >= 400 else None,
    }


async def _create(
    *,
    base: str,
    access_token: str,
    case: dict[str, Any],
    level: EscalationLevel,
    practitioner_reference: str,
) -> dict[str, Any]:
    payload = _build_payload(
        case=case,
        level=level,
        practitioner_reference=practitioner_reference,
    )

    attempted_at = now_iso()
    async with httpx.AsyncClient(timeout=25.0) as client:
        response = await client.post(
            f"{base}/Communication",
            headers=_headers(access_token),
            json=payload,
        )

    request_id = (
        response.headers.get("x-request-id")
        or response.headers.get("opc-request-id")
    )

    result: dict[str, Any] = {
        "profile": "smart_practitioner_required_only",
        "routingMode": "authenticated_smart_practitioner",
        "recipient": practitioner_reference,
        "sender": practitioner_reference,
        "httpStatus": response.status_code,
        "requestId": request_id,
        "attemptedAt": attempted_at,
    }

    if response.status_code >= 400:
        result.update({"status": "failed", "error": response.text[:1000]})
        return result

    try:
        resource = response.json()
    except ValueError:
        resource = {}

    location = response.headers.get("Location") or response.headers.get("Content-Location")
    resource_id = _extract_resource_id(resource, location)

    result.update(
        {
            "status": "created",
            "communicationId": resource_id or None,
            "location": location,
            "createdAt": now_iso(),
        }
    )

    if resource_id and _truthy("ORACLE_ESCALATION_FHIR_VERIFY_CREATED_RESOURCE", "true"):
        verification = await _verify_created(
            base=base,
            access_token=access_token,
            resource_id=resource_id,
        )
        result["verification"] = verification
        result["verificationStatus"] = verification.get("status")
        result["verificationHttpStatus"] = verification.get("httpStatus")
        result["verifiedAt"] = verification.get("verifiedAt")
        if verification.get("status") == "verified":
            result["status"] = "verified"

    return result


async def communication_readiness(
    *,
    token_state: dict[str, Any],
    level: EscalationLevel | str = EscalationLevel.L1_NURSING_REVIEW,
) -> dict[str, Any]:
    normalize_level(level)
    practitioner_reference, identity = await _resolve_smart_practitioner(token_state)

    patient_id = str(token_state.get("patient") or token_state.get("patient_id") or "").strip()

    state = "READY"
    problems: list[str] = []

    if not _truthy("ORACLE_ESCALATION_FHIR_ENABLED", "true"):
        state = "DISABLED"
    if not patient_id:
        problems.append("patient_context_missing")
    if not practitioner_reference:
        problems.append("smart_practitioner_unavailable")

    if problems and state != "DISABLED":
        state = "MISCONFIGURED"

    return {
        "state": state,
        "patientId": patient_id or None,
        "practitioner": identity,
        "sender": practitioner_reference or None,
        "recipient": practitioner_reference or None,
        "routingMode": "authenticated_smart_practitioner",
        "productionProfile": "smart_practitioner_required_only",
        "verifyCreatedResourceEnabled": _truthy(
            "ORACLE_ESCALATION_FHIR_VERIFY_CREATED_RESOURCE",
            "true",
        ),
        "problems": problems,
    }


async def test_fhir_communication(
    *,
    token_state: dict[str, Any],
    patient_id: str,
    recipient_mode: str = "smart_user",
    include_subject: bool = False,
    level: EscalationLevel | str = EscalationLevel.L1_NURSING_REVIEW,
) -> dict[str, Any]:
    # Legacy parameters remain accepted so stale local diagnostic URLs do not break.
    del recipient_mode
    del include_subject

    normalized = normalize_level(level)
    base = str(token_state.get("fhir_base_url") or "").strip().rstrip("/")
    access_token = str(token_state.get("access_token") or "").strip()
    practitioner_reference, identity = await _resolve_smart_practitioner(token_state)

    if not base or not access_token or not patient_id:
        return {"status": "failed", "reason": "oracle_fhir_context_incomplete"}

    if not practitioner_reference:
        return {
            "status": "failed",
            "reason": "oracle_smart_practitioner_unavailable",
            "practitioner": identity,
        }

    case = {
        "patientId": patient_id,
        "effectiveLevel": normalized.value,
        "modelResponse": {
            "episodeSummary": "CARDINAL Oracle FHIR Communication production-route test.",
            "primaryEtiology": "Connectivity validation",
        },
        "modelRationale": "Oracle sandbox Communication write-path validation.",
        "correlationId": "cardinal-oracle-fhir-production-test",
    }

    result = await _create(
        base=base,
        access_token=access_token,
        case=case,
        level=normalized,
        practitioner_reference=practitioner_reference,
    )

    return {**result, "practitioner": identity}


async def create_fhir_communication(
    *,
    token_state: dict[str, Any],
    case: dict[str, Any],
) -> dict[str, Any]:
    attempted_at = now_iso()

    if not _truthy("ORACLE_ESCALATION_FHIR_ENABLED", "true"):
        return {
            "status": "skipped",
            "reason": "oracle_fhir_communication_disabled",
            "attemptedAt": attempted_at,
        }

    base = str(token_state.get("fhir_base_url") or "").strip().rstrip("/")
    access_token = str(token_state.get("access_token") or "").strip()
    patient_id = str(case.get("patientId") or "").strip()

    if not base or not access_token or not patient_id:
        return {
            "status": "skipped",
            "reason": "oracle_fhir_context_incomplete",
            "attemptedAt": attempted_at,
        }

    level = normalize_level(case.get("effectiveLevel"))
    practitioner_reference, identity = await _resolve_smart_practitioner(token_state)

    if not practitioner_reference:
        return {
            "status": "failed",
            "reason": "oracle_smart_practitioner_unavailable",
            "practitioner": identity,
            "attemptedAt": attempted_at,
        }

    result = await _create(
        base=base,
        access_token=access_token,
        case=case,
        level=level,
        practitioner_reference=practitioner_reference,
    )

    return {**result, "practitioner": identity, "attemptedAt": attempted_at}
