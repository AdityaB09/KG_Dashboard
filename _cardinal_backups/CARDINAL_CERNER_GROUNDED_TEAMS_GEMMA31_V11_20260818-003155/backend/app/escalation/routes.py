from __future__ import annotations

import os
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.config import settings
from app.escalation.audit import append_audit_event
from app.escalation.epic.active_escalation import find_active_epic_escalation
from app.escalation.epic.cds_cards import build_escalation_card
from app.escalation.epic.cds_feedback import record_cds_feedback
from app.escalation.epic.cds_security import security_readiness, validate_epic_cds_request
from app.escalation.epic.cds_service import (
    discovery_document as epic_cds_discovery_document,
    hook_name as epic_cds_hook_name,
    public_urls as epic_cds_public_urls,
    service_id as epic_cds_service_id,
)
from app.escalation.models import now_iso, public_case
from app.escalation.orchestrator import escalation_orchestrator
from app.escalation.policy_engine import policy_engine
from app.escalation.repository import escalation_repository
from app.escalation.oracle.base_urls import message_api_base, personnel_api_base, recipient_api_base
from app.escalation.oracle.fhir_identity import discover_current_person, discover_practitioners
from app.escalation.oracle.fhir_communication import (
    communication_readiness,
    test_fhir_communication,
)
from app.escalation.oracle.fhir_user import resolve_smart_fhir_user
from app.escalation.oracle.group_inboxes import discover_group_inboxes
from app.escalation.oracle.personnel import discover_personnel
from app.escalation.oracle.system_auth import (
    OracleSystemAuthError,
    get_system_access_token,
    system_auth_readiness,
    test_system_token,
)
from app.oracle_smart import get_token_for_request
from app.oracle_token_refresh import ensure_fresh_oracle_token


router = APIRouter(tags=["cardinal-escalation"])


class ActorBody(BaseModel):
    actor: str | None = None
    role: str | None = None
    note: str | None = None


class EscalateBody(BaseModel):
    actor: str | None = None
    role: str | None = None
    reason: str | None = None


class AutoEscalationBody(BaseModel):
    enabled: bool


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


async def _oracle_context(request: Request) -> tuple[dict[str, Any], str, str, str | None]:
    token_state = get_token_for_request(request)
    if not token_state:
        raise HTTPException(
            status_code=401,
            detail="An active Oracle SMART session is required for sandbox discovery.",
        )
    await ensure_fresh_oracle_token(token_state)
    access_token = str(token_state.get("access_token") or "").strip()
    fhir_base_url = str(token_state.get("fhir_base_url") or os.getenv("ORACLE_FHIR_BASE_URL", "")).strip().rstrip("/")
    patient_id = str(token_state.get("patient") or token_state.get("patient_id") or "").strip() or None
    if not access_token:
        raise HTTPException(status_code=401, detail="Oracle SMART access token is unavailable.")
    return token_state, access_token, fhir_base_url, patient_id


async def _millennium_token() -> tuple[str, dict[str, Any]]:
    # Oracle FHIR and Millennium EHR APIs are separate resource servers.
    # Native Recipients/Messages now use the separate System application via
    # client_credentials. A legacy static bearer is accepted only as fallback.
    return await get_system_access_token()


def _state(enabled: bool, configured: bool, unavailable: bool = False) -> str:
    if unavailable:
        return "SANDBOX NOT AVAILABLE"
    if not enabled:
        return "OPTIONAL"
    return "READY" if configured else "MISCONFIGURED"


@router.get("/api/escalation/health")
async def escalation_health():
    return {
        "enabled": escalation_orchestrator.enabled,
        "caseCount": len(escalation_repository.list_cases()),
        "policy": policy_engine.public_summary(),
    }


@router.get("/api/escalation/readiness")
async def escalation_readiness():
    email_enabled = _truthy("ESCALATION_EMAIL_ENABLED")
    email_recipient = any(
        os.getenv(name, "").strip()
        for name in (
            "ESCALATION_EMAIL_CARE_TEAM", "ESCALATION_EMAIL_URGENT_PROVIDER",
            "ESCALATION_EMAIL_RRT", "ESCALATION_EMAIL_CODE",
            "ESCALATION_EMAIL_L1", "ESCALATION_EMAIL_L2",
            "ESCALATION_EMAIL_L3", "ESCALATION_EMAIL_L4",
        )
    )
    email_ready = bool(
        email_recipient
        and os.getenv("SMTP_HOST", "").strip()
        and os.getenv("SMTP_USERNAME", "").strip()
        and os.getenv("SMTP_PASSWORD", "").strip()
    )
    teams_enabled = _truthy("ESCALATION_TEAMS_ENABLED")
    teams_ready = bool(
        os.getenv("ESCALATION_TEAMS_WORKFLOW_URL", "").strip()
        or os.getenv("ESCALATION_TEAMS_WORKFLOWS_JSON", "").strip()
    )
    fhir_base = os.getenv("ORACLE_FHIR_BASE_URL", "").strip()
    recipient_base = recipient_api_base(fhir_base_url=fhir_base)
    message_base = message_api_base(fhir_base_url=fhir_base)
    personnel_base = personnel_api_base(fhir_base_url=fhir_base)
    oracle_system = system_auth_readiness()
    oracle_target_suffixes = {
        "CARE_TEAM_REVIEW": ("CARE_TEAM", "L1"),
        "URGENT_PROVIDER_REVIEW": ("URGENT_PROVIDER", "L2"),
        "RAPID_RESPONSE_ACTIVATION": ("RRT", "L3"),
        "CODE_RESPONSE_ACTIVATION": ("CODE", "L4"),
    }
    oracle_targets = {
        pathway: any(
            os.getenv(f"ORACLE_ESCALATION_TARGET_{suffix}_{field}", "").strip()
            for suffix in suffixes
            for field in ("ID", "NAME")
        )
        for pathway, suffixes in oracle_target_suffixes.items()
    }
    epic_native = _truthy("EPIC_CDS_NATIVE_WORKFLOW_AVAILABLE")
    public_base = os.getenv("EPIC_CDS_PUBLIC_BASE_URL", "").strip()
    return {
        "enabled": escalation_orchestrator.enabled,
        "policy": policy_engine.public_summary(),
        "automaticEscalationDefault": bool(settings.ESCALATION_AUTO_ADVANCE_DEFAULT),
        "legacyManualActionsEnabled": bool(settings.ESCALATION_LEGACY_MANUAL_ACTIONS_ENABLED),
        "email": {
            "state": _state(email_enabled, email_ready),
            "enabled": email_enabled,
            "mode": os.getenv("ESCALATION_EMAIL_MODE", "smtp"),
            "recipientConfigured": email_recipient,
        },
        "teams": {
            "state": _state(teams_enabled, teams_ready),
            "enabled": teams_enabled,
            "transport": "Microsoft Teams Workflows webhook",
            "workflowConfigured": teams_ready,
        },
        "oracle": {
            "state": "READY" if (recipient_base and message_base and personnel_base and oracle_system.get("configured")) else "MISCONFIGURED",
            "recipientApiBase": recipient_base,
            "messageApiBase": message_base,
            "personnelApiBase": personnel_base,
            "systemAuth": oracle_system,
            "messageSenderConfigured": bool(
                os.getenv("ORACLE_ESCALATION_MESSAGE_SENDER_PERSON_ID", "").strip()
                or os.getenv("ORACLE_ESCALATION_MESSAGE_SENDER_ID", "").strip()
            ),
            "targetsConfigured": oracle_targets,
            "fhirSenderConfigured": bool(
                os.getenv("ORACLE_ESCALATION_FHIR_SENDER_REFERENCE", "").strip()
                or os.getenv("ORACLE_ESCALATION_FHIR_PRACTITIONER_REFERENCE", "").strip()
            ),
            "discoveryEndpoint": "/api/escalation/oracle/sandbox-identities",
        },
        "epic": {
            "state": "READY" if public_base else "OPTIONAL",
            "cdsServiceId": epic_cds_service_id(),
            "cdsHook": epic_cds_hook_name(),
            "cdsEndpoint": "/api/integrations/epic/cds-hooks/escalation",
            "feedbackEndpoint": "/api/integrations/epic/cds-hooks/escalation/feedback",
            "discoveryEndpoint": "/api/integrations/epic/cds-hooks/cds-services",
            "standardServiceEndpoint": f"/api/integrations/epic/cds-hooks/cds-services/{epic_cds_service_id()}",
            "standardFeedbackEndpoint": f"/api/integrations/epic/cds-hooks/cds-services/{epic_cds_service_id()}/feedback",
            "publicBaseConfigured": bool(public_base),
            "nativeWorkflow": "READY" if epic_native else "SANDBOX NOT AVAILABLE",
            "security": security_readiness(),
        },
    }


@router.get("/api/escalation/policy")
async def escalation_policy():
    return policy_engine.public_summary()


@router.get("/api/escalation/oracle/system/readiness")
async def oracle_system_readiness():
    return system_auth_readiness()


@router.get("/api/escalation/oracle/system/token-test")
async def oracle_system_token_test():
    # Safe diagnostic: never returns the bearer token or client secret.
    return await test_system_token()


@router.get("/api/escalation/oracle/system/group-inboxes")
@router.get("/api/escalation/oracle/group-inboxes")
async def oracle_group_inboxes(name: str | None = Query(default=None)):
    try:
        access_token, token_meta = await _millennium_token()
    except OracleSystemAuthError as exc:
        return {
            "status": "failed",
            "stage": "system_token",
            "httpStatus": exc.http_status,
            "error": str(exc),
            "oracleResponse": exc.response_excerpt,
            "systemAuth": system_auth_readiness(),
            "items": [],
        }
    result = await discover_group_inboxes(access_token=access_token, name=name)
    result["systemAuth"] = token_meta
    if result.get("status") == "ready":
        result["nextStep"] = (
            "Copy a real returned Group Inbox id/name into "
            "ORACLE_ESCALATION_TARGET_CARE_TEAM / URGENT_PROVIDER / RRT / CODE (legacy L1-L4 keys also remain supported). "
            "One sandbox inbox may be reused for multiple CARDINAL levels."
        )
    return result


@router.get("/api/escalation/oracle/system/personnel")
@router.get("/api/escalation/oracle/personnel")
async def oracle_personnel(name: str | None = Query(default=None)):
    try:
        access_token, token_meta = await _millennium_token()
    except OracleSystemAuthError as exc:
        return {
            "status": "failed",
            "stage": "system_token",
            "httpStatus": exc.http_status,
            "error": str(exc),
            "oracleResponse": exc.response_excerpt,
            "systemAuth": system_auth_readiness(),
            "items": [],
        }
    result = await discover_personnel(
        access_token=access_token,
        free_text_name=name,
    )
    result["systemAuth"] = token_meta
    return result


@router.get("/api/escalation/oracle/fhir/practitioners")
async def oracle_fhir_practitioners(request: Request, name: str | None = Query(default=None)):
    _, access_token, fhir_base_url, _ = await _oracle_context(request)
    return await discover_practitioners(
        fhir_base_url=fhir_base_url,
        access_token=access_token,
        name=name,
    )


@router.get("/api/escalation/oracle/fhir/current-person")
async def oracle_fhir_current_person(request: Request):
    _, access_token, fhir_base_url, patient_id = await _oracle_context(request)
    if not patient_id:
        raise HTTPException(status_code=400, detail="The active Oracle SMART session has no patient context.")
    return await discover_current_person(
        fhir_base_url=fhir_base_url,
        access_token=access_token,
        patient_id=patient_id,
    )


@router.get("/api/escalation/oracle/fhir/current-user")
async def oracle_fhir_current_user(request: Request):
    token_state, _, _, _ = await _oracle_context(request)
    result = await resolve_smart_fhir_user(token_state)
    result["senderMode"] = os.getenv("ORACLE_ESCALATION_FHIR_SENDER_MODE", "smart_user")
    result["configuredSender"] = (
        os.getenv("ORACLE_ESCALATION_FHIR_SENDER_REFERENCE", "").strip()
        or os.getenv("ORACLE_ESCALATION_FHIR_PRACTITIONER_REFERENCE", "").strip()
        or None
    )
    result["communicationRule"] = (
        "For Provider SMART writes, CARDINAL uses the authenticated SMART Practitioner as sender."
    )
    return result


@router.get("/api/escalation/oracle/sandbox-identities")
async def oracle_sandbox_identities(request: Request):
    """One browser-friendly discovery surface for presentation setup.

    Each channel is allowed to report its own sandbox authorization limitation;
    one unsupported Oracle capability does not make the whole discovery call fail.
    """
    _, access_token, fhir_base_url, patient_id = await _oracle_context(request)
    millennium_token = ""
    system_token_meta: dict[str, Any] | None = None
    system_token_error: dict[str, Any] | None = None
    try:
        millennium_token, system_token_meta = await _millennium_token()
    except OracleSystemAuthError as exc:
        system_token_error = {
            "status": "failed",
            "stage": "system_token",
            "httpStatus": exc.http_status,
            "error": str(exc),
            "oracleResponse": exc.response_excerpt,
        }

    async def safe(call, *args, **kwargs):
        try:
            return await call(*args, **kwargs)
        except Exception as exc:
            return {"status": "sandbox_unavailable", "errorType": type(exc).__name__, "message": str(exc)}

    inboxes = (await safe(
        discover_group_inboxes,
        access_token=millennium_token,
        fhir_base_url=fhir_base_url,
    )) if millennium_token else (system_token_error or {"status": "misconfigured", "reason": "Oracle System messaging credentials are incomplete."})
    personnel = (await safe(
        discover_personnel,
        access_token=millennium_token,
        fhir_base_url=fhir_base_url,
    )) if millennium_token else (system_token_error or {"status": "misconfigured", "reason": "Oracle System messaging credentials are incomplete."})
    practitioners = await safe(
        discover_practitioners,
        fhir_base_url=fhir_base_url,
        access_token=access_token,
    )
    person = (
        await safe(
            discover_current_person,
            fhir_base_url=fhir_base_url,
            access_token=access_token,
            patient_id=patient_id,
        )
        if patient_id
        else {"status": "skipped", "reason": "no_patient_context"}
    )
    return {
        "fhirBaseUrl": fhir_base_url,
        "patientId": patient_id,
        "recipientApiBase": recipient_api_base(fhir_base_url=fhir_base_url),
        "messageApiBase": message_api_base(fhir_base_url=fhir_base_url),
        "personnelApiBase": personnel_api_base(fhir_base_url=fhir_base_url),
        "systemAuth": system_token_meta or system_auth_readiness(),
        "groupInboxes": inboxes,
        "personnel": personnel,
        "practitioners": practitioners,
        "currentPerson": person,
        "instructions": {
            "groupInbox": "Copy a real returned Group Inbox id/name into ORACLE_ESCALATION_TARGET_L*_ID/NAME.",
            "personSender": "Copy a valid sender PERSON identifier returned/authorized by the Millennium sandbox into ORACLE_ESCALATION_MESSAGE_SENDER_PERSON_ID.",
            "fhirPractitioner": "Copy a returned Practitioner/<id> into ORACLE_ESCALATION_FHIR_PRACTITIONER_REFERENCE (or per-level sender/recipient references).",
        },
    }


@router.get("/api/escalation/oracle/fhir/communication/readiness")
async def oracle_fhir_communication_readiness(request: Request):
    token_state, _, _, patient_id = await _oracle_context(request)
    result = await communication_readiness(token_state=token_state)
    result["patientId"] = patient_id or result.get("patientId")
    return result


@router.post("/api/escalation/oracle/fhir/communication/test")
async def oracle_fhir_communication_test(
    request: Request,
    level: str = Query(default="CARE_TEAM_REVIEW"),
):
    token_state, _, _, patient_id = await _oracle_context(request)
    if not patient_id:
        raise HTTPException(
            status_code=400,
            detail="The active Oracle SMART session has no patient context.",
        )

    return await test_fhir_communication(
        token_state=token_state,
        patient_id=patient_id,
        level=level,
    )


@router.get(
    "/api/escalation/oracle/fhir/communication/test-ui",
    response_class=HTMLResponse,
)
async def oracle_fhir_communication_test_ui():
    return HTMLResponse(
        """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>CARDINAL · Oracle FHIR Communication</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 1000px; margin: 32px auto; padding: 0 20px; }
    button { margin: 6px; padding: 10px 14px; }
    pre { background: #111827; color: #e5e7eb; padding: 16px; border-radius: 8px; overflow: auto; min-height: 280px; }
  </style>
</head>
<body>
  <h1>CARDINAL · Oracle FHIR Communication</h1>
  <p>Use this page after a fresh Oracle Provider SMART launch in the same browser.</p>
  <button onclick="run('/api/escalation/oracle/fhir/communication/readiness')">1. Check readiness</button>
  <button onclick="testProduction()">2. Test production Communication</button>
  <pre id="out">Ready.</pre>
<script>
const out = document.getElementById('out');
async function show(response) {
  const text = await response.text();
  try { out.textContent = JSON.stringify(JSON.parse(text), null, 2); }
  catch { out.textContent = text; }
}
async function run(url) {
  out.textContent = 'Loading...';
  await show(await fetch(url, {credentials: 'include'}));
}
async function testProduction() {
  out.textContent = 'Creating and verifying an Oracle sandbox Communication...';
  await show(await fetch(
    '/api/escalation/oracle/fhir/communication/test?level=CARE_TEAM_REVIEW',
    {method: 'POST', credentials: 'include'}
  ));
}
</script>
</body>
</html>"""
    )

@router.get("/api/escalation/active")
async def active_escalation(
    patientId: str = Query(...),
    encounterId: str | None = Query(default=None),
    provider: str | None = Query(default=None),
):
    case = escalation_repository.find_active(
        patient_id=patientId,
        encounter_id=encounterId,
        provider=provider,
    )
    return {"escalation": public_case(case) if case else None}


@router.get("/api/escalation/episode/{episode_id}")
async def escalation_for_episode(episode_id: str):
    case = escalation_repository.find_by_episode(episode_id)
    return {"escalation": public_case(case) if case else None}


@router.get("/api/escalation/incident/{incident_id}")
async def escalation_for_incident(incident_id: str):
    case = escalation_repository.find_by_incident(incident_id)
    return {"escalation": public_case(case) if case else None}


@router.get("/api/integrations/epic/cds-hooks/readiness")
async def epic_cds_readiness():
    urls = epic_cds_public_urls()
    return {
        "state": "READY" if urls.get("publicBaseUrl") else "OPTIONAL",
        **urls,
        # Compatibility fields retained for existing CARDINAL tooling.
        "serviceUrl": urls.get("directServiceUrl"),
        "feedbackUrl": urls.get("directFeedbackUrl"),
        "serviceId": epic_cds_service_id(),
        "hook": epic_cds_hook_name(),
        "security": security_readiness(),
        "nativeWorkflow": "READY" if _truthy("EPIC_CDS_NATIVE_WORKFLOW_AVAILABLE") else "SANDBOX NOT AVAILABLE",
    }


@router.get("/api/integrations/epic/cds-hooks/cds-services")
async def epic_cds_services():
    """Standard CDS Hooks service discovery document."""
    return epic_cds_discovery_document()


async def _run_epic_cds_escalation(request: Request, payload: dict[str, Any]):
    started = time.perf_counter()
    security = validate_epic_cds_request(request)
    requested_hook = str(payload.get("hook") or "").strip()
    configured_hook = epic_cds_hook_name()
    if requested_hook and requested_hook != configured_hook:
        raise HTTPException(
            status_code=400,
            detail=f"This CARDINAL CDS service is configured for hook '{configured_hook}', not '{requested_hook}'.",
        )
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    patient_id = str(
        context.get("patientId")
        or context.get("patient")
        or payload.get("patientId")
        or ""
    ).strip()
    encounter_id = str(
        context.get("encounterId")
        or context.get("encounter")
        or payload.get("encounterId")
        or ""
    ).strip() or None
    if not patient_id:
        return {"cards": []}

    case = find_active_epic_escalation(
        patient_id=patient_id,
        encounter_id=encounter_id,
    )
    if not case:
        return {"cards": []}

    event_id = str(case.get("eventId") or "")
    append_audit_event(
        event_id,
        "EPIC_CDS_HOOK_INVOKED",
        detail="Epic CDS request matched an active CARDINAL escalation.",
        data={
            "hook": payload.get("hook"),
            "hookInstance": payload.get("hookInstance"),
            "patientId": patient_id,
            "encounterId": encounter_id,
            "requestCorrelationId": security.correlation_id,
            "jwtValidated": security.enabled,
            "jwtIssuer": security.issuer,
            "jwtSubject": security.subject,
        },
        delivery_result="INVOKED",
    )
    current = escalation_repository.get(event_id) or case
    card = build_escalation_card(current)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    def store_card(value: dict[str, Any]) -> None:
        value.setdefault("epicCdsCards", []).append(
            {
                "cardUuid": card.get("uuid"),
                "hookInstance": payload.get("hookInstance"),
                "returnedAt": now_iso(),
                "responseMs": elapsed_ms,
                "correlationId": security.correlation_id,
            }
        )

    escalation_repository.update(event_id, store_card)
    append_audit_event(
        event_id,
        "EPIC_CDS_CARD_RETURNED",
        detail=str(card.get("summary") or "CARDINAL CDS card returned."),
        data={
            "cardUuid": card.get("uuid"),
            "hookInstance": payload.get("hookInstance"),
            "responseMs": elapsed_ms,
            "requestCorrelationId": security.correlation_id,
        },
        external_vendor_id=str(card.get("uuid") or ""),
        delivery_result="DELIVERED",
        http_status=200,
    )
    return {"cards": [card]}


@router.post("/api/integrations/epic/cds-hooks/escalation")
async def epic_cds_escalation(request: Request, payload: dict[str, Any]):
    # Existing direct endpoint retained so current Epic/manual configuration does not break.
    return await _run_epic_cds_escalation(request, payload)


@router.post("/api/integrations/epic/cds-hooks/cds-services/{service_id}")
async def epic_cds_standard_service(
    service_id: str,
    request: Request,
    payload: dict[str, Any],
):
    if service_id != epic_cds_service_id():
        raise HTTPException(status_code=404, detail="Unknown CARDINAL CDS service ID.")
    return await _run_epic_cds_escalation(request, payload)


async def _handle_epic_feedback(request: Request, payload: dict[str, Any]):
    security = validate_epic_cds_request(request)
    return record_cds_feedback(payload, correlation_id=security.correlation_id)


@router.post("/api/integrations/epic/cds-hooks/escalation/feedback")
async def epic_cds_feedback_service(request: Request, payload: dict[str, Any]):
    return await _handle_epic_feedback(request, payload)


@router.post("/api/integrations/epic/cds-hooks/feedback")
async def epic_cds_feedback_compat(request: Request, payload: dict[str, Any]):
    return await _handle_epic_feedback(request, payload)


@router.post("/api/integrations/epic/cds-hooks/cds-services/{service_id}/feedback")
async def epic_cds_standard_feedback(
    service_id: str,
    request: Request,
    payload: dict[str, Any],
):
    if service_id != epic_cds_service_id():
        raise HTTPException(status_code=404, detail="Unknown CARDINAL CDS service ID.")
    return await _handle_epic_feedback(request, payload)


@router.get("/api/escalation/{event_id}")
async def get_escalation(event_id: str):
    case = escalation_repository.get(event_id)
    if not case:
        raise HTTPException(status_code=404, detail="Escalation case was not found.")
    return public_case(case)


@router.post("/api/escalation/{event_id}/auto-escalation")
async def set_auto_escalation(event_id: str, body: AutoEscalationBody):
    try:
        return escalation_orchestrator.set_auto_escalation(event_id, enabled=body.enabled)
    except KeyError:
        raise HTTPException(status_code=404, detail="Clinical response case was not found.")


@router.post("/api/escalation/{event_id}/acknowledge", deprecated=True)
async def acknowledge_escalation(event_id: str, body: ActorBody | None = None):
    if not settings.ESCALATION_LEGACY_MANUAL_ACTIONS_ENABLED:
        raise HTTPException(
            status_code=410,
            detail="Manual acknowledgement was retired by the V10 hospital-response workflow. Use site routing and the automatic escalation toggle instead.",
        )
    try:
        return escalation_orchestrator.acknowledge(event_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Clinical response case was not found.")


@router.post("/api/escalation/{event_id}/escalate", deprecated=True)
async def manually_escalate(event_id: str, body: EscalateBody | None = None):
    if not settings.ESCALATION_LEGACY_MANUAL_ACTIONS_ENABLED:
        raise HTTPException(
            status_code=410,
            detail="Manual escalation was retired by the V10 workflow. Enable Automatic Escalation for site-controlled pathway advancement.",
        )
    if not escalation_repository.get(event_id):
        raise HTTPException(status_code=404, detail="Clinical response case was not found.")
    reason = body.reason if body and body.reason else "legacy_manual_escalation"
    case = await escalation_orchestrator.escalate(event_id, reason=reason)
    return public_case(case)


@router.post("/api/escalation/{event_id}/resolve", deprecated=True)
async def resolve_escalation(event_id: str, body: ActorBody | None = None):
    if not settings.ESCALATION_LEGACY_MANUAL_ACTIONS_ENABLED:
        raise HTTPException(
            status_code=410,
            detail="Manual resolution was retired by the V10 hospital-response workflow.",
        )
    try:
        return escalation_orchestrator.resolve(event_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Clinical response case was not found.")

