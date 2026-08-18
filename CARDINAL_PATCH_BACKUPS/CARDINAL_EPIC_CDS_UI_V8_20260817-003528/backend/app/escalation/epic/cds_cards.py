from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

from app.escalation.levels import EscalationLevel, level_label, normalize_level

TOPIC_SYSTEM = "https://cardinal.local/cds-hooks/topics"
TOPIC_CODE = "cardinal-clinical-escalation"


def _frontend_base() -> str:
    return os.getenv("FRONTEND_APP_URL", "http://127.0.0.1:5173").strip().rstrip("/")


def _indicator(level: EscalationLevel) -> str:
    if level in {EscalationLevel.L3_RAPID_RESPONSE_REVIEW, EscalationLevel.L4_EMERGENCY_RESPONSE}:
        return "critical"
    if level == EscalationLevel.L2_URGENT_PROVIDER_REVIEW:
        return "warning"
    return "info"


def _clinical_reason(case: dict[str, Any]) -> str:
    model = case.get("modelResponse") if isinstance(case.get("modelResponse"), dict) else {}
    episode_summary = str(model.get("episodeSummary") or "").strip()
    etiology = str(model.get("primaryEtiology") or "").strip()
    rationale = str(case.get("modelRationale") or "").strip()
    reason = episode_summary or etiology or rationale or "CARDINAL has an active escalation for this patient."
    # CDS card stays concise; full model output belongs on the CARDINAL escalation page.
    if len(reason) > 360:
        reason = reason[:357].rstrip() + "..."
    return reason


def build_escalation_card(case: dict[str, Any]) -> dict[str, Any]:
    level = normalize_level(case.get("effectiveLevel"))
    status = str(case.get("status") or "").upper()
    acknowledged = status == "ACKNOWLEDGED"
    label = level_label(level)
    reason = _clinical_reason(case)
    detail = f"{reason}\n\nCARDINAL effective escalation: {level.value} · {label}."
    if acknowledged:
        detail += "\n\nResponse acknowledged in CARDINAL."

    return {
        "uuid": str(uuid4()),
        "summary": f"CARDINAL — {label}" + (" — Acknowledged" if acknowledged else ""),
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
            {
                "label": "Open CARDINAL Escalation",
                "url": f"{_frontend_base()}/escalation/{case.get('eventId')}",
                "type": "smart",
                "appContext": f"cardinalEscalationEventId={case.get('eventId')}",
            }
        ],
    }
