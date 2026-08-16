from __future__ import annotations

import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo


def require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main() -> None:
    recipient = require("ALERT_EMAIL")
    username = require("SMTP_USERNAME")
    password = require("SMTP_PASSWORD")
    reason = require("REASON")
    report = require("REPORT")
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))

    now = datetime.now(ZoneInfo("America/Chicago"))

    msg = EmailMessage()
    msg["Subject"] = f"[CARDINAL] Safety shutdown triggered: {reason}"
    msg["From"] = username
    msg["To"] = recipient
    msg.set_content(
        f"""CARDINAL Environment Safety Guard

Time: {now:%Y-%m-%d %I:%M:%S %p %Z}
Reason: {reason}

One or more CARDINAL Cloud Run services were still accessible.
AUTO with min=0 is intentionally considered OPEN because a request can wake it.

State before shutdown:
{report}

The guard will now force all three services to MANUAL / 0:
- kg-dashboard-frontend
- kg-dashboard-backend
- cardinal-gemma4-26b-a4b-it

This action is independent of billing-budget thresholds.
"""
    )

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(msg)

    print("Alert email sent successfully.")


if __name__ == "__main__":
    main()
