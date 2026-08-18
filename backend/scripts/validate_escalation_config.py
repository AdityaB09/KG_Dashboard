from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.escalation.policy_engine import policy_engine


def value(name: str) -> str:
    return os.getenv(name, "").strip()


def configured(name: str) -> bool:
    text = value(name)
    return bool(text) and "PASTE_YOUR_" not in text and "REPLACE_WITH_" not in text


def line(label: str, ok: bool, detail: str = "") -> None:
    mark = "OK" if ok else "MISSING"
    print(f"[{mark:7}] {label}{': ' + detail if detail else ''}")


print("CARDINAL escalation configuration validation")
print("=" * 56)
policy = policy_engine.public_summary()
line("Policy loaded", policy.get("policyId") == "CARDINAL-HOSPITAL-V1", f"{policy.get('policyId')} v{policy.get('version')}")
line("Escalation enabled", value("ESCALATION_ENABLED").lower() in {"1", "true", "yes", "on"})
line("Gemini model", value("SLM_MODEL") == "gemini-3.6-flash", value("SLM_MODEL"))
line("Gemini API key", configured("SLM_API_KEY"))
line("Live V7.1", value("ETIOLOGY_V7_LIVE_MODEL_ENABLED").lower() in {"1", "true", "yes", "on"})
line("Precomputed V7.1 disabled", value("ETIOLOGY_V7_PRECOMPUTED_ENABLED").lower() in {"0", "false", "no", "off"})
print()
print("Common notification channel")
line("SMTP host", configured("SMTP_HOST"), value("SMTP_HOST"))
line("SMTP username", configured("SMTP_USERNAME"), value("SMTP_USERNAME"))
line("Gmail app password", configured("SMTP_PASSWORD"))
for level in ("L1", "L2", "L3", "L4"):
    line(f"Email {level}", configured(f"ESCALATION_EMAIL_{level}"), value(f"ESCALATION_EMAIL_{level}"))
print()
print("Oracle native routing (optional until sandbox IDs are configured)")
line("Millennium API base", configured("ORACLE_MILLENNIUM_API_BASE_URL"))
line("Message sender PERSON ID", configured("ORACLE_ESCALATION_MESSAGE_SENDER_ID"))
for level in ("L1", "L2", "L3", "L4"):
    ok = configured(f"ORACLE_ESCALATION_TARGET_{level}_ID") or configured(f"ORACLE_ESCALATION_TARGET_{level}_NAME")
    line(f"Oracle target {level}", ok)
line("FHIR sender Practitioner", configured("ORACLE_ESCALATION_FHIR_SENDER_REFERENCE"))
print()
print("Epic CDS Hooks")
line("Epic CDS service ID", configured("EPIC_CDS_SERVICE_ID"), value("EPIC_CDS_SERVICE_ID"))
line("Public CDS base", configured("EPIC_CDS_PUBLIC_BASE_URL"), "required only for real Epic server-to-server CDS invocation")
print()
critical = ["SLM_API_KEY", "SMTP_PASSWORD"]
missing = [name for name in critical if not configured(name)]
if missing:
    print("Complete the common workflow by filling: " + ", ".join(missing))
    raise SystemExit(2)
print("Common Gemini + email escalation workflow is configured.")
