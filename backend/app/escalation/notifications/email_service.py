from __future__ import annotations

import asyncio
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from app.config import settings
from app.escalation.levels import level_label, normalize_level
from app.escalation.models import now_iso
from app.escalation.notifications.recipient_directory import recipient_directory


def _frontend_base() -> str:
    return os.getenv("FRONTEND_APP_URL", "http://127.0.0.1:5173").strip().rstrip("/")


def _patient_display(case: dict[str, Any]) -> str:
    response = case.get("modelResponse") or {}
    summary = str(response.get("episodeSummary") or "").strip()
    patient = str(case.get("patientDisplayName") or case.get("patientId") or "").strip()
    return patient or (summary.split(",", 1)[0] if summary else "Patient")


def _build_message(case: dict[str, Any], recipient: dict[str, str]) -> EmailMessage:
    level = normalize_level(case.get("effectiveLevel"))
    response = case.get("modelResponse") or {}
    event_id = str(case.get("eventId") or "")
    provider = str(case.get("provider") or "cardinal").lower()
    platform = (
        "Oracle Health" if provider == "oracle"
        else "Epic" if provider == "epic"
        else "CARDINAL"
    )
    link = f"{_frontend_base()}/escalation/{event_id}"

    message = EmailMessage()
    message["Subject"] = f"CARDINAL — {level.value.split('_', 1)[0]} {level_label(level)}"
    message["To"] = recipient["email"]
    sender = os.getenv("ESCALATION_EMAIL_FROM", os.getenv("SMTP_USERNAME", "cardinal@localhost")).strip()
    display_name = os.getenv("ESCALATION_EMAIL_FROM_NAME", "CARDINAL Clinical Escalation").strip()
    message["From"] = f"{display_name} <{sender}>" if display_name else sender
    message["Reply-To"] = os.getenv("ESCALATION_EMAIL_REPLY_TO", sender).strip() or sender
    message["X-CARDINAL-Event-ID"] = event_id
    if case.get("correlationId"):
        message["X-CARDINAL-Correlation-ID"] = str(case.get("correlationId"))
    message.set_content(
        "\n".join(
            [
                "CARDINAL Clinical Escalation",
                "",
                f"Platform: {platform}",
                f"Patient: {_patient_display(case)}",
                f"Episode: {response.get('rhythm') or ''}",
                f"Escalation: {level.value} — {level_label(level)}",
                "",
                "Episode Summary",
                str(response.get("episodeSummary") or ""),
                "",
                "Primary Etiology",
                str(response.get("primaryEtiology") or ""),
                "",
                "Escalation Reason",
                str(case.get("modelRationale") or ""),
                "",
                "Open Escalation",
                link,
            ]
        )
    )
    return message


def _send_smtp(message: EmailMessage) -> dict[str, Any]:
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        raise RuntimeError("SMTP_HOST is not configured.")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    use_ssl = os.getenv("SMTP_SSL", "false").lower() in {"1", "true", "yes", "on"}
    use_starttls = os.getenv("SMTP_STARTTLS", "true").lower() in {"1", "true", "yes", "on"}

    smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_cls(host, port, timeout=20) as client:
        if not use_ssl and use_starttls:
            client.starttls()
        if username:
            client.login(username, password)
        client.send_message(message)
    return {"status": "sent", "transport": "smtp"}


def _write_file(message: EmailMessage, event_id: str) -> dict[str, Any]:
    configured = Path(os.getenv("ESCALATION_EMAIL_OUTBOX_PATH", "data/escalation_outbox"))
    if not configured.is_absolute():
        configured = Path(__file__).resolve().parents[3] / configured
    configured.mkdir(parents=True, exist_ok=True)
    path = configured / f"{event_id}.eml"
    path.write_bytes(bytes(message))
    return {"status": "written", "transport": "file", "path": str(path)}


class EscalationEmailService:
    async def send(self, case: dict[str, Any]) -> dict[str, Any]:
        level = normalize_level(case.get("effectiveLevel"))
        if level.value == "L0_MONITOR":
            return {"status": "not_required"}

        recipient = recipient_directory.resolve(level)
        if not recipient:
            return {"status": "skipped", "reason": "recipient_not_configured"}

        if not settings.ESCALATION_EMAIL_ENABLED:
            return {
                "status": "skipped",
                "reason": "email_disabled",
                "recipientRole": recipient.get("role"),
                "recipient": recipient.get("email"),
            }

        message = _build_message(case, recipient)
        mode = os.getenv("ESCALATION_EMAIL_MODE", "smtp").strip().lower()
        try:
            if mode == "file":
                result = await asyncio.to_thread(_write_file, message, str(case.get("eventId")))
            else:
                result = await asyncio.to_thread(_send_smtp, message)
            return {
                **result,
                "recipient": recipient["email"],
                "recipientRole": recipient.get("role"),
                "subject": message["Subject"],
                "sentAt": now_iso(),
            }
        except Exception as exc:
            fallback = os.getenv("ESCALATION_EMAIL_FALLBACK_TO_FILE", "true").strip().lower() in {"1", "true", "yes", "on"}
            fallback_result = None
            if fallback and mode != "file":
                try:
                    fallback_result = await asyncio.to_thread(_write_file, message, str(case.get("eventId")))
                except Exception:
                    fallback_result = None
            return {
                "status": "failed",
                "transport": mode,
                "recipient": recipient["email"],
                "recipientRole": recipient.get("role"),
                "errorType": type(exc).__name__,
                "error": str(exc),
                "fileFallback": fallback_result,
            }


email_service = EscalationEmailService()
