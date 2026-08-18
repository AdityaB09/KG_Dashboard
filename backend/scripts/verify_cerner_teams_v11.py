from __future__ import annotations

import json
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app.escalation.levels import (  # noqa: E402
    EscalationLevel,
    is_auto_advance_terminal,
    next_level,
    normalize_level,
    tier_code,
)

fail = 0


def check(condition: bool, message: str) -> None:
    global fail
    if condition:
        print(f"[PASS] {message}")
    else:
        fail += 1
        print(f"[FAIL] {message}")


# Functional response-tier contract.
expected = {
    "T0_MONITOR": EscalationLevel.MONITOR_ONLY,
    "T1_CLINICAL_REVIEW": EscalationLevel.CARE_TEAM_REVIEW,
    "T2_URGENT_REVIEW": EscalationLevel.URGENT_PROVIDER_REVIEW,
    "T3_RAPID_RESPONSE": EscalationLevel.RAPID_RESPONSE_ACTIVATION,
    "E_EMERGENCY_OVERRIDE": EscalationLevel.CODE_RESPONSE_ACTIVATION,
}
for code, level in expected.items():
    check(normalize_level(code) == level, f"normalized tier accepted: {code}")
    check(tier_code(level) == code, f"normalized tier emitted: {code}")

check(
    next_level(EscalationLevel.RAPID_RESPONSE_ACTIVATION) == EscalationLevel.RAPID_RESPONSE_ACTIVATION,
    "Rapid Response does not automatically advance into Emergency Override",
)
check(is_auto_advance_terminal(EscalationLevel.RAPID_RESPONSE_ACTIVATION), "T3 is terminal for timer-based auto progression")
check(is_auto_advance_terminal(EscalationLevel.CODE_RESPONSE_ACTIVATION), "Emergency Override is terminal for timer-based auto progression")

# Site-configurable policy contract.
policy_path = BACKEND / "app" / "escalation" / "policies" / "oracle_millennium_hospital_response_v1.json"
policy = json.loads(policy_path.read_text(encoding="utf-8"))
profile = policy.get("responseTierProfile") or {}
check(profile.get("profileId") == "cardinal-cerner-grounded-t0-t3-e-v1", "Cerner-grounded normalized response profile installed")
check("not official Oracle/Cerner" in str(profile.get("claimBoundary", "")), "policy explicitly avoids claiming official Cerner tiers")
windows = policy.get("autoAdvanceSecondsByLevel") or {}
check(all(int(value or 0) == 0 for value in windows.values()), "no fabricated default response timers are enabled")
mu_ref = (policy.get("referenceAutomationExamples") or {}).get("MU_PROVIDER_BEDSIDE_15_MINUTES") or {}
check(int(mu_ref.get("seconds") or 0) == 900, "MU 15-minute provider-bedside timing retained only as optional reference")
check("Do not apply this universally" in str(mu_ref.get("note", "")), "MU reference timing is explicitly non-universal")

# Teams transport contract.
teams_path = BACKEND / "app" / "escalation" / "notifications" / "teams_service.py"
teams = teams_path.read_text(encoding="utf-8")
for key in (
    "ESCALATION_TEAMS_WORKFLOW_T1",
    "ESCALATION_TEAMS_WORKFLOW_T2",
    "ESCALATION_TEAMS_WORKFLOW_T3",
    "ESCALATION_TEAMS_WORKFLOW_E",
):
    check(key in teams, f"Teams per-tier workflow key supported: {key}")
for channel in ("clinical-review", "urgent-review", "rapid-response", "emergency-response"):
    check(channel in teams, f"Teams channel label present: {channel}")
check('"type": "message"' in teams, "Teams Workflows message envelope installed")
check('application/vnd.microsoft.card.adaptive' in teams, "Teams Adaptive Card attachment installed")
check('"type": "AdaptiveCard"' in teams, "Adaptive Card content installed")
check('ESCALATION_TEAMS_INCLUDE_PATIENT_IDENTIFIERS' in teams, "Teams patient-identifier safety switch installed")

# Direct private Cloud Run authentication contract inherited from verified V10.
auth_path = BACKEND / "app" / "cloud_run_auth.py"
auth = auth_path.read_text(encoding="utf-8")
check("gcloud_cli" in auth, "direct local gcloud identity-token auth remains supported")
check("print-identity-token" in auth, "gcloud identity-token command remains installed")

# Model contract.
etiology_path = BACKEND / "app" / "evaluation_injection" / "etiology_v7.py"
etiology = etiology_path.read_text(encoding="utf-8")
for code in expected:
    check(code in etiology, f"Etiology prompt supports normalized response tier {code}")
check("not official oracle/cerner" in etiology.lower(), "Etiology prompt avoids claiming vendor-defined Cerner levels")
check("do not advance from t3" in etiology.lower(), "Etiology prompt keeps emergency override condition-driven")

# Frontend terminology contract.
page = (PROJECT / "src" / "components" / "EscalationPage.jsx").read_text(encoding="utf-8")
status = (PROJECT / "src" / "components" / "EscalationStatusCard.jsx").read_text(encoding="utf-8")
check("Normalized Response Tier" in page, "response page displays normalized tier")
check("Reference Severity Band" in page, "response page displays reference severity band")
check("Automatic Escalation" in page, "response page retains ON/OFF automatic escalation switch")
check("T3" in status and '"E"' in status, "compact response widget uses T0/T1/T2/T3/E labels")

print(f"FAIL={fail}")
print("RESULT=PASS" if fail == 0 else "RESULT=FAIL")
raise SystemExit(0 if fail == 0 else 1)
