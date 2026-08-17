from __future__ import annotations

import json
import os
from typing import Any

from app.escalation.audit import append_audit_event
from app.escalation.levels import EscalationLevel, normalize_level
from app.escalation.oracle.fhir_communication import create_fhir_communication
from app.escalation.oracle.fhir_identity import discover_current_person
from app.escalation.oracle.group_inboxes import discover_group_inboxes, find_group_inbox
from app.escalation.oracle.group_messaging import send_group_message
from app.escalation.oracle.recipient_validation import validate_recipient
from app.escalation.oracle.system_auth import OracleSystemAuthError, get_system_access_token
from app.oracle_smart import get_token_for_session_id
from app.oracle_token_refresh import ensure_fresh_oracle_token


def _frontend_base() -> str:
    return os.getenv("FRONTEND_APP_URL", "http://127.0.0.1:5173").strip().rstrip("/")


def _targets() -> dict[str, Any]:
    raw = os.getenv("ORACLE_ESCALATION_TARGETS_JSON", "{}").strip() or "{}"
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _configured_target(level: EscalationLevel) -> dict[str, str] | None:
    value = _targets().get(level.value)
    if isinstance(value, dict):
        target_type = str(value.get("type") or "GROUPINBOX").strip().upper()
        target_id = str(value.get("id") or "").strip()
        target_name = str(value.get("name") or "").strip()
        if target_id or target_name:
            return {"type": target_type, "id": target_id, "name": target_name}

    short = level.value.split("_", 1)[0]
    target_id = os.getenv(f"ORACLE_ESCALATION_TARGET_{short}_ID", "").strip()
    target_name = os.getenv(f"ORACLE_ESCALATION_TARGET_{short}_NAME", "").strip()
    target_type = os.getenv(f"ORACLE_ESCALATION_TARGET_{short}_TYPE", "GROUPINBOX").strip().upper()
    if target_id or target_name:
        return {"type": target_type, "id": target_id, "name": target_name}
    return None


def _extract_identifier(item: dict[str, Any]) -> str:
    for key in ("id", "identifier", "value", "recipientId", "groupInboxId"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, dict):
            nested = value.get("value") or value.get("id")
            if nested:
                return str(nested).strip()
    return ""


class OracleEscalationAdapter:
    async def dispatch(self, case: dict[str, Any]) -> dict[str, Any]:
        event_id = str(case.get("eventId") or "")
        routing = case.get("routingContext") or {}
        session_id = str(routing.get("smartSessionId") or "").strip()
        token_state = get_token_for_session_id(session_id)
        if not token_state:
            return {"status": "failed", "reason": "oracle_smart_session_unavailable"}

        try:
            refresh = await ensure_fresh_oracle_token(token_state)
        except Exception as exc:
            return {
                "status": "failed",
                "reason": "oracle_token_unavailable",
                "errorType": type(exc).__name__,
                "error": str(exc),
            }

        access_token = str(token_state.get("access_token") or "").strip()
        # FHIR and Millennium EHR APIs are separate resource servers. The
        # Provider SMART token remains exclusively for FHIR. Native Recipients/
        # Messages calls use the separate confidential System app through
        # client_credentials and a cached System token.
        fhir_base_url = str(token_state.get("fhir_base_url") or "").strip()
        millennium_access_token = ""
        system_token_meta: dict[str, Any] | None = None
        level = normalize_level(case.get("effectiveLevel"))
        target = _configured_target(level)
        result: dict[str, Any] = {"status": "started", "tokenRefresh": refresh}

        append_audit_event(
            event_id,
            "ORACLE_FHIR_COMMUNICATION_ATTEMPTED",
            detail="Oracle FHIR Communication delivery was evaluated.",
        )
        try:
            fhir_result = await create_fhir_communication(token_state=token_state, case=case)
        except Exception as exc:
            fhir_result = {
                "status": "failed",
                "errorType": type(exc).__name__,
                "error": str(exc),
            }
        verification = fhir_result.get("verification") or {}
        result["fhirCommunication"] = {
            **fhir_result,
            "verificationStatus": (
                fhir_result.get("verificationStatus")
                or verification.get("status")
            ),
            "verificationHttpStatus": (
                fhir_result.get("verificationHttpStatus")
                or verification.get("httpStatus")
            ),
            "verifiedAt": (
                fhir_result.get("verifiedAt")
                or verification.get("verifiedAt")
            ),
        }
        if fhir_result.get("status") in {"created", "verified"}:
            append_audit_event(
                event_id,
                "ORACLE_FHIR_COMMUNICATION_CREATED",
                detail="Oracle FHIR Communication was created.",
                external_vendor_id=str(fhir_result.get("communicationId") or "") or None,
                http_status=fhir_result.get("httpStatus"),
                data={"channel": "fhirCommunication", "result": fhir_result},
            )
        if fhir_result.get("status") == "verified" or (fhir_result.get("verification") or {}).get("status") == "verified":
            append_audit_event(
                event_id,
                "ORACLE_FHIR_COMMUNICATION_VERIFIED",
                detail="Oracle FHIR Communication was retrieved successfully after creation.",
                external_vendor_id=str(fhir_result.get("communicationId") or "") or None,
                http_status=(fhir_result.get("verification") or {}).get("httpStatus"),
                data={"channel": "fhirCommunication", "verification": fhir_result.get("verification")},
            )

        if not target:
            result["groupMessaging"] = {"status": "skipped", "reason": "oracle_target_not_configured"}
            result["status"] = "partial"
            return result

        try:
            millennium_access_token, system_token_meta = await get_system_access_token()
            result["systemMessagingAuth"] = system_token_meta
        except OracleSystemAuthError as exc:
            result["groupMessaging"] = {
                "status": "failed",
                "reason": "oracle_system_token_unavailable",
                "httpStatus": exc.http_status,
                "error": str(exc),
                "oracleResponse": exc.response_excerpt,
            }
            result["status"] = "partial"
            return result

        target_id = target.get("id") or ""
        if not target_id and target.get("name") and target.get("type") == "GROUPINBOX":
            discovery = await discover_group_inboxes(
                access_token=millennium_access_token,
                fhir_base_url=fhir_base_url,
                name=target["name"],
            )
            result["groupInboxDiscovery"] = discovery
            if discovery.get("status") == "ready":
                match = find_group_inbox(discovery.get("items"), name=target["name"])
                target_id = _extract_identifier(match or {})
                if match:
                    result["resolvedTarget"] = {
                        "type": "GROUPINBOX",
                        "id": target_id or None,
                        "name": target.get("name"),
                    }
                    append_audit_event(
                        event_id,
                        "ORACLE_GROUP_INBOX_DISCOVERED",
                        detail=f"Oracle Group Inbox resolved: {target.get('name')}",
                        external_vendor_id=target_id or None,
                        http_status=discovery.get("httpStatus"),
                        data={"recipientId": target_id, "recipientName": target.get("name")},
                    )

        if not target_id:
            result["groupMessaging"] = {"status": "failed", "reason": "oracle_target_id_unresolved"}
            result["status"] = "partial"
            return result

        validation = await validate_recipient(
            access_token=millennium_access_token,
            recipient_type=target["type"],
            recipient_id=target_id,
            patient_id=str(case.get("patientId") or ""),
            fhir_base_url=fhir_base_url,
        )
        result["recipientValidation"] = validation
        if validation.get("status") == "validated" and validation.get("isValid") is not False:
            append_audit_event(
                event_id,
                "ORACLE_RECIPIENT_VALIDATED",
                detail="Oracle Message Center recipient validation succeeded.",
                external_vendor_id=target_id,
                http_status=validation.get("httpStatus"),
                data={
                    "recipientId": target_id,
                    "recipientName": target.get("name"),
                    "recipientType": target.get("type"),
                    "isValid": validation.get("isValid"),
                },
            )
        if validation.get("status") in {"failed", "invalid"}:
            result["groupMessaging"] = {"status": "not_sent", "reason": "recipient_validation_failed"}
            result["status"] = "partial"
            return result

        sender_person_id = (
            os.getenv("ORACLE_ESCALATION_MESSAGE_SENDER_PERSON_ID", "").strip()
            or os.getenv("ORACLE_ESCALATION_MESSAGE_SENDER_ID", "").strip()
        )
        if not sender_person_id:
            try:
                person_result = await discover_current_person(
                    fhir_base_url=fhir_base_url,
                    access_token=access_token,
                    patient_id=str(case.get("patientId") or ""),
                )
            except Exception as exc:
                person_result = {
                    "status": "failed",
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                }
            result["senderPersonDiscovery"] = person_result
            if person_result.get("status") == "ready":
                sender_person_id = str(person_result.get("personId") or "").strip()

        open_url = f"{_frontend_base()}/escalation/{case.get('eventId')}"
        message = await send_group_message(
            access_token=millennium_access_token,
            case=case,
            recipient_type=target["type"],
            recipient_id=target_id,
            open_url=open_url,
            fhir_base_url=fhir_base_url,
            sender_person_id=sender_person_id or None,
        )
        message = {
            **message,
            "recipientName": (
                message.get("recipientName")
                or target.get("name")
            ),
        }
        result["groupMessaging"] = message
        if message.get("status") == "sent":
            append_audit_event(
                event_id,
                "ORACLE_GROUP_MESSAGE_SENT",
                detail="Oracle Message Center patient message was accepted.",
                external_vendor_id=str(message.get("messageId") or "") or None,
                http_status=message.get("httpStatus"),
                delivery_result="sent",
                data={
                    "recipientId": target_id,
                    "recipientName": target.get("name"),
                    "recipientType": target.get("type"),
                    "messageId": message.get("messageId"),
                    "priority": message.get("priority"),
                },
            )
        result["status"] = "accepted" if message.get("status") == "sent" else "partial"
        return result


oracle_escalation_adapter = OracleEscalationAdapter()
