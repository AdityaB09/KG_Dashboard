from __future__ import annotations

import json
import os
import py_compile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"

PASS = 0
WARN = 0
FAIL = 0


def check(ok: bool, message: str, *, warn: bool = False):
    global PASS, WARN, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {message}")
    elif warn:
        WARN += 1
        print(f"[WARN] {message}")
    else:
        FAIL += 1
        print(f"[FAIL] {message}")


def contains(path: Path, *needles: str) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    return all(n in text for n in needles)


required = [
    BACKEND / "app/escalation/oracle/base_urls.py",
    BACKEND / "app/escalation/oracle/fhir_identity.py",
    BACKEND / "app/escalation/oracle/personnel.py",
    BACKEND / "app/escalation/epic/cds_security.py",
    ROOT / "src/components/EscalationStatusCard.jsx",
    ROOT / "src/components/EscalationPage.jsx",
]
for path in required:
    check(path.exists(), f"Required hardening file exists: {path.relative_to(ROOT)}")

check(contains(ROOT / "src/components/EscalationStatusCard.jsx", "RESPONSE", "ACK Pending", "Details"), "Analytics uses compact RESPONSE escalation strip")
check(contains(ROOT / "src/components/EscalationPage.jsx", "Delivery Channels", "Response State", "Policy Rules Fired"), "Dedicated escalation page contains operational detail sections")
check(contains(BACKEND / "app/escalation/oracle/base_urls.py", "ORACLE_RECIPIENT_API_BASE_URL", "ORACLE_MESSAGE_API_BASE_URL", "ORACLE_PERSONNEL_API_BASE_URL"), "Oracle EHR API namespaces are separated")
check(contains(BACKEND / "app/escalation/oracle/group_inboxes.py", "recipient_api_base"), "Oracle Group Inbox discovery uses recipient namespace")
check(contains(BACKEND / "app/escalation/oracle/recipient_validation.py", "message_api_base", '"MESSAGES"'), "Oracle recipient validation uses message namespace and MESSAGES category")
check(contains(BACKEND / "app/escalation/oracle/group_messaging.py", "message_api_base", '"PERSON"', '"GROUPINBOX"'), "Oracle patient message send uses Message API contract")
check(contains(BACKEND / "app/escalation/oracle/fhir_communication.py", "Practitioner/", "Communication", "contentAttachment"), "Oracle FHIR Communication adapter is hardened")
check(contains(BACKEND / "app/escalation/routes.py", "/api/escalation/oracle/sandbox-identities", "/api/integrations/epic/cds-hooks/escalation/feedback"), "Oracle discovery and Epic service feedback routes exist")
check(contains(BACKEND / "app/escalation/epic/cds_cards.py", '"uuid"', '"topic"', "cardinal-clinical-escalation"), "Epic CDS cards include UUID and stable source topic")
check(contains(BACKEND / "app/escalation/epic/cds_security.py", "Authorization", "jti", "PyJWKClient"), "Optional Epic CDS JWT validation exists")
check(contains(BACKEND / "app/escalation/epic/adapter.py", "EPIC_ROUTING_ACTIVE"), "Epic routing-active audit is distinct from hook invocation")
check(contains(BACKEND / "app/escalation/routes.py", "EPIC_CDS_HOOK_INVOKED", "EPIC_CDS_CARD_RETURNED"), "Epic CDS invocation/card audit proof exists")
check(contains(BACKEND / "app/escalation/oracle/adapter.py", "ORACLE_FHIR_COMMUNICATION_VERIFIED", "ORACLE_GROUP_INBOX_DISCOVERED", "ORACLE_GROUP_MESSAGE_SENT"), "Oracle provider-specific audit events exist")

# Non-regression guard: this hardening should not structurally replace the waveform page.
seven = ROOT / "src/components/SevenLeadWaveformPage.jsx"
check(seven.exists(), "SevenLeadWaveformPage remains present")
check(contains(ROOT / "src/components/ClinicalPhysiologyPage.jsx", "EscalationStatusCard"), "ClinicalPhysiologyPage still mounts escalation additively")

# Environment readiness. Missing optional sandbox IDs are warnings, not install failures.
env = BACKEND / ".env"
env_text = env.read_text(encoding="utf-8", errors="replace") if env.exists() else ""
check("# CARDINAL ESCALATION HARDENING V2" in env_text, "Backend hardening ENV block appended")
front_env = ROOT / "src/.env"
front_text = front_env.read_text(encoding="utf-8", errors="replace") if front_env.exists() else ""
check("VITE_ESCALATION_COUNTDOWN_TICK_MS" in front_text, "Frontend countdown ENV setting appended")

for key, label in [
    ("ORACLE_ESCALATION_MESSAGE_SENDER_PERSON_ID", "Oracle PERSON sender ID"),
    ("ORACLE_ESCALATION_FHIR_PRACTITIONER_REFERENCE", "Oracle sandbox Practitioner reference"),
]:
    configured = any(line.startswith(f"{key}=") and line.split("=", 1)[1].strip() for line in env_text.splitlines())
    check(configured, f"{label} configured", warn=True)

target_ready = any(
    line.startswith("ORACLE_ESCALATION_TARGET_L3_ID=") and line.split("=",1)[1].strip()
    for line in env_text.splitlines()
) or any(
    line.startswith("ORACLE_ESCALATION_TARGET_L3_NAME=") and line.split("=",1)[1].strip()
    for line in env_text.splitlines()
)
check(target_ready, "Oracle Group Inbox target configured", warn=True)

public_epic = any(line.startswith("EPIC_CDS_PUBLIC_BASE_URL=") and line.split("=",1)[1].strip() for line in env_text.splitlines())
check(public_epic, "Epic public CDS/ngrok base configured", warn=True)

# Compile every hardening Python module.
try:
    for path in (BACKEND / "app/escalation").rglob("*.py"):
        py_compile.compile(str(path), doraise=True)
    check(True, "Escalation backend Python compiles")
except Exception as exc:
    check(False, f"Escalation backend compilation failed: {exc}")

print("\n=== CARDINAL HARDENING VERIFIER ===")
print(f"PASS={PASS} WARN={WARN} FAIL={FAIL}")
print("RESULT=" + ("PASS" if FAIL == 0 else "FAIL"))
sys.exit(0 if FAIL == 0 else 1)
