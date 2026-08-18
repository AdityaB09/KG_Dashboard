from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.escalation.levels import (
    EscalationLevel,
    level_label,
    level_role,
    normalize_level,
    oracle_priority,
    reference_band,
    tier_code,
)
from app.escalation.models import now_iso


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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


_SPECIFIC_WORKFLOW_KEYS: dict[EscalationLevel, tuple[str, ...]] = {
    EscalationLevel.CARE_TEAM_REVIEW: (
        "ESCALATION_TEAMS_WORKFLOW_T1",
        "ESCALATION_TEAMS_WORKFLOW_CLINICAL_REVIEW",
    ),
    EscalationLevel.URGENT_PROVIDER_REVIEW: (
        "ESCALATION_TEAMS_WORKFLOW_T2",
        "ESCALATION_TEAMS_WORKFLOW_URGENT_REVIEW",
    ),
    EscalationLevel.RAPID_RESPONSE_ACTIVATION: (
        "ESCALATION_TEAMS_WORKFLOW_T3",
        "ESCALATION_TEAMS_WORKFLOW_RAPID_RESPONSE",
    ),
    EscalationLevel.CODE_RESPONSE_ACTIVATION: (
        "ESCALATION_TEAMS_WORKFLOW_E",
        "ESCALATION_TEAMS_WORKFLOW_EMERGENCY",
    ),
}

_DEFAULT_CHANNEL_LABELS: dict[EscalationLevel, str] = {
    EscalationLevel.CARE_TEAM_REVIEW: "clinical-review",
    EscalationLevel.URGENT_PROVIDER_REVIEW: "urgent-review",
    EscalationLevel.RAPID_RESPONSE_ACTIVATION: "rapid-response",
    EscalationLevel.CODE_RESPONSE_ACTIVATION: "emergency-response",
}

_CHANNEL_ENV_KEYS: dict[EscalationLevel, str] = {
    EscalationLevel.CARE_TEAM_REVIEW: "ESCALATION_TEAMS_CHANNEL_T1",
    EscalationLevel.URGENT_PROVIDER_REVIEW: "ESCALATION_TEAMS_CHANNEL_T2",
    EscalationLevel.RAPID_RESPONSE_ACTIVATION: "ESCALATION_TEAMS_CHANNEL_T3",
    EscalationLevel.CODE_RESPONSE_ACTIVATION: "ESCALATION_TEAMS_CHANNEL_E",
}


def _workflow_url(level: Any) -> str:
    normalized = normalize_level(level)

    # V11's explicit per-tier keys are easiest to maintain and avoid JSON escaping
    # in .env.  Existing V10 JSON/global keys remain valid fallbacks.
    for key in _SPECIFIC_WORKFLOW_KEYS.get(normalized, ()):
        value = os.getenv(key, "").strip()
        if value:
            return value

    mappings = _workflow_map()
    for key, value in mappings.items():
        try:
            if normalize_level(key) == normalized:
                return value
        except ValueError:
            continue
    return os.getenv("ESCALATION_TEAMS_WORKFLOW_URL", "").strip()


def _channel_label(level: Any) -> str:
    normalized = normalize_level(level)
    key = _CHANNEL_ENV_KEYS.get(normalized)
    configured = os.getenv(key, "").strip() if key else ""
    return configured or _DEFAULT_CHANNEL_LABELS.get(normalized, "clinical-response")


def _safe_destination(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "configured-workflow"


def _patient_display(case: dict[str, Any]) -> str:
    if not _truthy("ESCALATION_TEAMS_INCLUDE_PATIENT_IDENTIFIERS", False):
        return "Synthetic/demo patient" if _truthy("ESCALATION_TEAMS_SYNTHETIC_DEMO", True) else "Patient"
    return str(case.get("patientDisplayName") or case.get("patientId") or "Patient").strip()


def _plain_text(value: Any, limit: int = 450) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    return text[:limit]


def _adaptive_card(case: dict[str, Any]) -> dict[str, Any]:
    level = normalize_level(case.get("effectiveLevel"))
    response = case.get("modelResponse") if isinstance(case.get("modelResponse"), dict) else {}
    event_id = str(case.get("eventId") or "")
    t_code = tier_code(level)
    band = reference_band(level)
    channel = _channel_label(level)
    priority = oracle_priority(level)

    facts = [
        {"title": "Response tier", "value": t_code},
        {"title": "Pathway", "value": level_label(level)},
        {"title": "Assigned role", "value": level_role(level)},
        {"title": "Reference band", "value": band},
        {"title": "Oracle message priority", "value": f"{priority} ({'STAT' if priority == 'HIGH' else 'ROUTINE'})"},
        {"title": "Target channel", "value": channel},
        {"title": "Platform", "value": str(case.get("provider") or "CARDINAL").title()},
    ]

    if _truthy("ESCALATION_TEAMS_INCLUDE_PATIENT_IDENTIFIERS", False):
        facts.insert(0, {"title": "Patient", "value": _patient_display(case)})

    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": f"CARDINAL · {t_code.replace('_', ' ')}",
            "weight": "Bolder",
            "size": "Large",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": level_label(level),
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
        },
        {
            "type": "FactSet",
            "facts": facts,
        },
    ]

    episode_summary = _plain_text(response.get("episodeSummary"), 500)
    rhythm = _plain_text(response.get("rhythm"), 220)
    etiology = _plain_text(response.get("primaryEtiology"), 360)
    rationale = _plain_text(case.get("modelRationale"), 500)

    if episode_summary:
        body.append({"type": "TextBlock", "text": f"**Episode summary**\n{episode_summary}", "wrap": True})
    if rhythm:
        body.append({"type": "TextBlock", "text": f"**Rhythm**\n{rhythm}", "wrap": True})
    if etiology:
        body.append({"type": "TextBlock", "text": f"**Primary etiology**\n{etiology}", "wrap": True})
    if rationale:
        body.append({"type": "TextBlock", "text": f"**Routing rationale**\n{rationale}", "wrap": True})

    actions: list[dict[str, Any]] = []
    if event_id:
        actions.append(
            {
                "type": "Action.OpenUrl",
                "title": "Open CARDINAL response",
                "url": f"{_frontend_base()}/escalation/{event_id}",
            }
        )

    # This is the exact TeamsIncomingWebhookTrigger adaptive-card envelope
    # documented by Microsoft for the Workflows webhook trigger.
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.2",
                    "body": body,
                    "actions": actions,
                },
            }
        ],
    }


class TeamsWorkflowService:
    """Deliver a response notification to a Microsoft Teams Workflows webhook.

    V11 targets standard Microsoft Teams work/school/small-business Team channels.
    It does not claim support for Teams Free Communities.  Webhook URLs are secrets:
    only the hostname and configured channel label are retained in audit output.
    """

    async def send(self, case: dict[str, Any]) -> dict[str, Any]:
        level = normalize_level(case.get("effectiveLevel"))
        if level == EscalationLevel.MONITOR_ONLY:
            return {"status": "not_required", "tierCode": tier_code(level)}
        if not bool(getattr(settings, "ESCALATION_TEAMS_ENABLED", False)):
            return {"status": "skipped", "reason": "teams_disabled", "tierCode": tier_code(level)}
        url = _workflow_url(level)
        if not url:
            return {
                "status": "skipped",
                "reason": "teams_workflow_not_configured",
                "tierCode": tier_code(level),
                "channelLabel": _channel_label(level),
            }

        timeout = max(3.0, float(os.getenv("ESCALATION_TEAMS_TIMEOUT_SECONDS", "12")))
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=_adaptive_card(case))
                response.raise_for_status()
            return {
                "status": "sent",
                "transport": "teams_workflows_adaptive_card",
                "httpStatus": response.status_code,
                "destination": _safe_destination(url),
                "channelLabel": _channel_label(level),
                "tierCode": tier_code(level),
                "referenceSeverityBand": reference_band(level),
                "sentAt": now_iso(),
                "assignedRole": level_role(level),
            }
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            return {
                "status": "failed",
                "transport": "teams_workflows_adaptive_card",
                "httpStatus": status,
                "destination": _safe_destination(url),
                "channelLabel": _channel_label(level),
                "tierCode": tier_code(level),
                "errorType": type(exc).__name__,
                "error": str(exc)[:500],
            }


teams_workflow_service = TeamsWorkflowService()
