from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/fhir+json",
    }


def _bundle_resources(payload: Any, resource_type: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    resources: list[dict[str, Any]] = []
    for entry in payload.get("entry") or []:
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if isinstance(resource, dict) and resource.get("resourceType") == resource_type:
            resources.append(resource)
    return resources


def _display_name(resource: dict[str, Any]) -> str:
    names = resource.get("name") or []
    if isinstance(names, list) and names:
        name = names[0] if isinstance(names[0], dict) else {}
        family = str(name.get("family") or "").strip()
        given = " ".join(str(v).strip() for v in (name.get("given") or []) if str(v).strip())
        return ", ".join(v for v in (family, given) if v)
    return str(resource.get("id") or "")


async def discover_practitioners(
    *,
    fhir_base_url: str,
    access_token: str,
    name: str | None = None,
    active: bool = True,
    count: int = 20,
) -> dict[str, Any]:
    base = str(fhir_base_url or "").strip().rstrip("/")
    if not base or not access_token:
        return {"status": "failed", "reason": "oracle_fhir_context_incomplete", "items": []}
    params: dict[str, str] = {"_count": str(max(1, min(count, 50)))}
    if str(name or "").strip():
        params["name"] = str(name).strip()
    else:
        params["active"] = "true" if active else "false"
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{base}/Practitioner/",
            headers=_headers(access_token),
            params=params,
        )
    if response.status_code >= 400:
        result = {
            "status": "failed",
            "httpStatus": response.status_code,
            "error": response.text[:1000],
            "items": [],
        }
        if response.status_code == 403 and "insufficient_scope" in response.text:
            result["requiredScopeHint"] = "user/Practitioner.rs"
            result["nextStep"] = "Request a fresh Oracle SMART authorization after updating ORACLE_SCOPES."
        return result
    payload = response.json()
    items = [
        {
            "id": str(resource.get("id") or ""),
            "reference": f"Practitioner/{resource.get('id')}",
            "display": _display_name(resource),
            "active": resource.get("active"),
        }
        for resource in _bundle_resources(payload, "Practitioner")
        if resource.get("id")
    ]
    return {
        "status": "ready",
        "httpStatus": response.status_code,
        "items": items,
        "requestId": response.headers.get("x-request-id") or response.headers.get("opc-request-id"),
    }


async def discover_current_person(
    *,
    fhir_base_url: str,
    access_token: str,
    patient_id: str,
) -> dict[str, Any]:
    """Resolve a sandbox Person by matching the current Patient identifiers.

    This intentionally does not guess that Patient and Person logical IDs are the
    same. It searches Person using identifiers returned by the selected Patient.
    """
    base = str(fhir_base_url or "").strip().rstrip("/")
    patient_id = str(patient_id or "").strip()
    if not base or not access_token or not patient_id:
        return {"status": "failed", "reason": "oracle_fhir_context_incomplete"}

    headers = _headers(access_token)
    async with httpx.AsyncClient(timeout=20.0) as client:
        patient_response = await client.get(
            f"{base}/Patient/{quote(patient_id, safe='')}",
            headers=headers,
        )
        if patient_response.status_code >= 400:
            return {
                "status": "failed",
                "stage": "patient_read",
                "httpStatus": patient_response.status_code,
                "error": patient_response.text[:1000],
            }
        patient = patient_response.json()
        candidates: list[tuple[str, str]] = []
        for identifier in patient.get("identifier") or []:
            if not isinstance(identifier, dict):
                continue
            system = str(identifier.get("system") or "").strip()
            value = str(identifier.get("value") or "").strip()
            if system and value:
                candidates.append((system, value))

        attempts: list[dict[str, Any]] = []
        for system, value in candidates[:12]:
            person_response = await client.get(
                f"{base}/Person",
                headers=headers,
                params={"identifier": f"{system}|{value}"},
            )
            attempts.append({
                "system": system,
                "httpStatus": person_response.status_code,
            })
            if person_response.status_code >= 400:
                if person_response.status_code == 403 and "insufficient_scope" in person_response.text:
                    return {
                        "status": "failed",
                        "stage": "person_search",
                        "httpStatus": person_response.status_code,
                        "error": person_response.text[:1000],
                        "requiredScopeHint": "user/Person.rs",
                        "nextStep": "Request a fresh Oracle SMART authorization after updating ORACLE_SCOPES.",
                        "attempts": attempts,
                    }
                continue
            resources = _bundle_resources(person_response.json(), "Person")
            if resources:
                person = resources[0]
                return {
                    "status": "ready",
                    "patientId": patient_id,
                    "personId": str(person.get("id") or ""),
                    "personReference": f"Person/{person.get('id')}",
                    "matchedIdentifierSystem": system,
                    "matchedIdentifierValue": value,
                    "attempts": attempts,
                }

    return {
        "status": "not_found",
        "patientId": patient_id,
        "attempts": attempts,
        "reason": "No Person matched the selected Patient identifiers with the active SMART authorization.",
    }
