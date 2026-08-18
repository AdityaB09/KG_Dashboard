from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.escalation.levels import level_label, level_role, normalize_level, oracle_priority
from app.escalation.models import now_iso


def _frontend_base() -> str:
    return os.getenv("FRONTEND_APP_URL", "http://127.0.0.1:5173").strip().rstrip("/")


def _workflow_map() -> dict[str, str]:
    raw = os.getenv("ESCALATION_TEAMS_WORKFLOWS_JSON", "{}").strip() or "{}"
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v).strip() for k, v in value.items() if str(v).strip()}


def _workflow_url(level: Any) -> str:
    normalized = normalize_level(level)
    mappings = _workflow_map()
    # Accept canonical and legacy keys in the user's existing local .env.
    for key, value in mappings.items():
        try:
            if normalize_level(key) == normalized:
                return value
        except ValueError:
            continue
    return os.getenv("ESCALATION_TEAMS_WORKFLOW_URL", "").strip()


def _safe_destination(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "configured-workflow"


def _patient_display(case: dict[str, Any]) -> str:
    return str(case.get("patientDisplayName") or case.get("patientId") or "Patient").strip()


def _payload(case: dict[str, Any]) -> dict[str, Any]:
    level = normalize_level(case.get("effectiveLevel"))
    response = case.get("modelResponse") if isinstance(case.get("modelResponse"), dict) else {}
    event_id = str(case.get("eventId") or "")
    return {
        "eventType": "cardinal.clinical_response",
        "schemaVersion": "cardinal-teams-workflow-v1",
        "eventId": event_id,
        "correlationId": case.get("correlationId"),
        "provider": case.get("provider"),
        "patientId": case.get("patientId"),
        "encounterId": case.get("encounterId"),
        "patientDisplayName": _patient_display(case),
        "responsePathway": level.value,
        "responseLabel": level_label(level),
        "assignedRole": level_role(level),
        "messagePriority": oracle_priority(level),
        "episodeSummary": str(response.get("episodeSummary") or ""),
        "rhythm": str(response.get("rhythm") or ""),
        "primaryEtiology": str(response.get("primaryEtiology") or ""),
        "reason": str(case.get("modelRationale") or ""),
        "openCaseUrl": f"{_frontend_base()}/escalation/{event_id}",
        "createdAt": now_iso(),
    }


class TeamsWorkflowService:
    """Deliver one clinical response notification to a Teams Workflow webhook.

    Microsoft recommends the Teams Workflows webhook trigger for new webhook-based
    channel integrations as Microsoft 365 Connectors are being retired.  The URL is
    treated as a secret and is never returned to the browser/audit record.
    """

    async def send(self, case: dict[str, Any]) -> dict[str, Any]:
        level = normalize_level(case.get("effectiveLevel"))
        if level.value == "MONITOR_ONLY":
            return {"status": "not_required"}
        if not bool(getattr(settings, "ESCALATION_TEAMS_ENABLED", False)):
            return {"status": "skipped", "reason": "teams_disabled"}
        url = _workflow_url(level)
        if not url:
            return {"status": "skipped", "reason": "teams_workflow_not_configured"}

        timeout = max(3.0, float(os.getenv("ESCALATION_TEAMS_TIMEOUT_SECONDS", "12")))
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=_payload(case))
                response.raise_for_status()
            return {
                "status": "sent",
                "transport": "teams_workflow_webhook",
                "httpStatus": response.status_code,
                "destination": _safe_destination(url),
                "sentAt": now_iso(),
                "assignedRole": level_role(level),
            }
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            return {
                "status": "failed",
                "transport": "teams_workflow_webhook",
                "httpStatus": status,
                "destination": _safe_destination(url),
                "errorType": type(exc).__name__,
                "error": str(exc)[:500],
            }


teams_workflow_service = TeamsWorkflowService()
