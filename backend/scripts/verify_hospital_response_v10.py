from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKS: list[tuple[bool, str]] = []


def check(condition: bool, label: str) -> None:
    CHECKS.append((bool(condition), label))
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")


levels = (ROOT / "app/escalation/levels.py").read_text(encoding="utf-8")
routes = (ROOT / "app/escalation/routes.py").read_text(encoding="utf-8")
orch = (ROOT / "app/escalation/orchestrator.py").read_text(encoding="utf-8")
teams = (ROOT / "app/escalation/notifications/teams_service.py").read_text(encoding="utf-8")
cloud_auth = (ROOT / "app/cloud_run_auth.py").read_text(encoding="utf-8")
etiology = (ROOT / "app/evaluation_injection/etiology_v7.py").read_text(encoding="utf-8")
frontend = (ROOT.parent / "src/components/EscalationPage.jsx").read_text(encoding="utf-8")
status_card = (ROOT.parent / "src/components/EscalationStatusCard.jsx").read_text(encoding="utf-8")
service = (ROOT.parent / "src/services/escalationService.js").read_text(encoding="utf-8")
policy = json.loads((ROOT / "app/escalation/policies/oracle_millennium_hospital_response_v1.json").read_text(encoding="utf-8"))

canonical = [
    "MONITOR_ONLY",
    "CARE_TEAM_REVIEW",
    "URGENT_PROVIDER_REVIEW",
    "RAPID_RESPONSE_ACTIVATION",
    "CODE_RESPONSE_ACTIVATION",
]
for code in canonical:
    check(code in levels, f"canonical response pathway present: {code}")

check("Oracle Health Millennium does not define a universal numbered L1-L4" in levels,
      "implementation explicitly avoids claiming a universal Oracle/Cerner L1-L4 ladder")
check(policy.get("status") == "site-configurable-reference", "policy is labeled site-configurable reference")
check(policy.get("policyId") == "ORACLE-MILLENNIUM-HOSPITAL-RESPONSE-V1", "new policy id installed")
check(policy.get("vendorSemantics", {}).get("oracleMessagePriority", {}).get("RAPID_RESPONSE_ACTIVATION") == "HIGH",
      "Rapid Response maps to Oracle HIGH/STAT priority")
check(policy.get("vendorSemantics", {}).get("oracleMessagePriority", {}).get("CODE_RESPONSE_ACTIVATION") == "HIGH",
      "Code response maps to Oracle HIGH/STAT priority")

check('/api/escalation/{event_id}/auto-escalation' in routes, "automatic-escalation toggle API installed")
check("Manual acknowledgement was retired" in routes, "legacy acknowledge API disabled by default")
check("Manual resolution was retired" in routes, "legacy resolve API disabled by default")
check("AUTO_ESCALATION_WINDOW_EXPIRED" in orch, "automatic pathway progression audit installed")
check("TEAMS_SENT" in orch, "Teams delivery is part of orchestrated response")
check("ESCALATION_TEAMS_WORKFLOW_URL" in teams, "Teams Workflows webhook transport installed")
check("ESCALATION_TEAMS_WORKFLOWS_JSON" in teams, "per-pathway Teams workflow mapping supported")
check("gcloud auth print-identity-token" in cloud_auth, "direct local gcloud identity-token auth supported")
check('elif mode == "gcloud_cli"' in cloud_auth, "gcloud_cli auth mode implemented")

check("Acknowledge" not in frontend, "Acknowledge button removed from response page")
check(">Resolve<" not in frontend and '"Resolve"' not in frontend, "Resolve button removed from response page")
check("role=\"switch\"" in frontend and "Automatic Escalation" in frontend, "automatic escalation ON/OFF switch present")
check("Microsoft Teams" in frontend, "Teams delivery evidence visible on response page")
check('CODE_RESPONSE_ACTIVATION: { short: "CODE"' in status_card and 'RAPID_RESPONSE_ACTIVATION: { short: "RRT"' in status_card, "compact response UI displays hospital pathway names instead of L1-L4")
check("setAutoEscalation" in service, "frontend service calls auto-escalation API")

check("RAPID_RESPONSE_ACTIVATION" in etiology and "CODE_RESPONSE_ACTIVATION" in etiology,
      "Etiology contract emits canonical hospital response pathways")
check("ten_field" in etiology.lower(), "Etiology V7.1 ten-field contract remains present")

failed = [label for ok, label in CHECKS if not ok]
print(f"FAIL={len(failed)}")
print("RESULT=PASS" if not failed else "RESULT=FAIL")
if failed:
    raise SystemExit(1)
