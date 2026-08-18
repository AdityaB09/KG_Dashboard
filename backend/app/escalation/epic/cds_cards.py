from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

from app.escalation.levels import EscalationLevel, level_label, normalize_level

TOPIC_SYSTEM = "https://cardinal.local/cds-hooks/topics"
TOPIC_CODE = "cardinal-clinical-escalation"


def _frontend_base() -> str:
    return os.getenv("FRONTEND_APP_URL", "http://127.0.0.1:5173").strip().rstrip("/")


def _link_type() -> str:
    configured = os.getenv("EPIC_CDS_LINK_TYPE", "absolute").strip().lower()
    return configured if configured in {"absolute", "smart"} else "absolute"


def _indicator(level: EscalationLevel) -> str:
    if level in {EscalationLevel.RAPID_RESPONSE_ACTIVATION, EscalationLevel.CODE_RESPONSE_ACTIVATION}:
        return "critical"
    if level == EscalationLevel.URGENT_PROVIDER_REVIEW:
        return "warning"
    return "info"


def _clinical_reason(case: dict[str, Any]) -> str:
    model = case.get("modelResponse") if isinstance(case.get("modelResponse"), dict) else {}
    episode_summary = str(model.get("episodeSummary") or "").strip()
    etiology = str(model.get("primaryEtiology") or "").strip()
    rationale = str(case.get("modelRationale") or "").strip()
    reason = episode_summary or etiology or rationale or "CARDINAL has an active clinical response pathway for this patient."
    # CDS card stays concise; full model output belongs on the CARDINAL escalation page.
    if len(reason) > 360:
        reason = reason[:357].rstrip() + "..."
    return reason


def _escalation_link(case: dict[str, Any]) -> dict[str, Any]:
    link_type = _link_type()
    link: dict[str, Any] = {
        "label": "Open CARDINAL Clinical Response",
        "url": f"{_frontend_base()}/escalation/{case.get('eventId')}",
        "type": link_type,
    }
    if link_type == "smart":
        link["appContext"] = f"cardinalEscalationEventId={case.get('eventId')}"
    return link


def build_escalation_card(case: dict[str, Any]) -> dict[str, Any]:
    level = normalize_level(case.get("effectiveLevel"))
    label = level_label(level)
    reason = _clinical_reason(case)
    detail = f"{reason}\n\nActive hospital response pathway: {label} · {case.get('assignedRole') or ''}."
    if case.get("autoEscalationEnabled") and case.get("nextEscalationAt"):
        detail += "\n\nAutomatic escalation is enabled for this case."

    return {
        "uuid": str(uuid4()),
        "summary": f"CARDINAL — {label}",
        "indicator": _indicator(level),
        "detail": detail,
        "source": {
            "label": "CARDINAL",
            "topic": {
                "system": TOPIC_SYSTEM,
                "code": TOPIC_CODE,
                "display": "Clinical Escalation",
            },
        },
        "links": [
            _escalation_link(case)
        ],
    }
