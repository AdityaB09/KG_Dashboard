from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path


EXPECTED_SCENARIOS = {
    "VFIB-STEMI-001",
    "TORSADES-LQT-002",
    "VT-ISCHEMIC-003",
    "AFIB-RVR-SEPSIS-004",
    "CHB-HYPERK-005",
    "BRADY-DIGTOX-006",
    "SVT-PSVT-007",
    "NSVT-ECTOPY-008",
    "WCT-DIFF-009",
    "WPW-AFIB-010",
    "FLUTTER-IC-011",
    "PERI-STEMI-012",
    "BRASH-013",
    "AMIO-DDI-014",
}


def env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        values[k.strip()] = v.strip()
    return values


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    backend = root / "backend"
    env = env_values(backend / ".env")
    checks: list[tuple[bool, str]] = []

    fhir = backend / "app/escalation/oracle/fhir_communication.py"
    routes = backend / "app/escalation/routes.py"
    group = backend / "app/escalation/oracle/group_messaging.py"
    orchestrator = backend / "app/escalation/orchestrator.py"
    service = backend / "app/evaluation_demo/service.py"
    mapping_path = backend / "app/evaluation_demo/patient_scenario_map.json"

    for path in (fhir, routes, group, orchestrator, service, mapping_path):
        checks.append((path.exists(), f"Required file exists: {path.relative_to(root)}"))

    for path in (fhir, routes, group, orchestrator, service):
        try:
            ast.parse(path.read_text(encoding="utf-8"))
            checks.append((True, f"Python syntax: {path.relative_to(root)}"))
        except Exception as exc:
            checks.append((False, f"Python syntax: {path.relative_to(root)} ({exc})"))

    fhir_text = fhir.read_text(encoding="utf-8")
    routes_text = routes.read_text(encoding="utf-8")
    group_text = group.read_text(encoding="utf-8")
    orchestrator_text = orchestrator.read_text(encoding="utf-8")
    service_text = service.read_text(encoding="utf-8")

    checks += [
        ("required_only" in fhir_text, "FHIR strict required-only profile installed"),
        ("smart_user_fallback" in fhir_text, "FHIR SMART-user recipient fallback installed"),
        ("required_plus_subject" in fhir_text, "FHIR +Patient-subject controlled retry installed"),
        ("ORACLE_ESCALATION_FHIR_VERIFY_CREATED_RESOURCE" in fhir_text, "FHIR GET verification installed"),
        ("/api/escalation/oracle/fhir/communication/test-ui" in routes_text, "FHIR browser diagnostic UI registered"),
        ("Unable to convert HTML to RTF" in group_text or "_is_html_to_rtf_error" in group_text, "Working Message Center XHTML/RTF retry preserved"),
        ("oracle_escalation_adapter.dispatch" in orchestrator_text, "Oracle vendor dispatch still wired"),
        ("email_service.send" in orchestrator_text, "Email delivery still wired"),
        ("smartSessionId" in service_text, "Oracle SMART session is retained for routing only"),
        ("token_override=None" in service_text, "Oracle SMART token is not injected into SLM clinical context"),
        (env.get("FRONTEND_APP_URL") in {"http://127.0.0.1:5173", "http://localhost:5173"}, "Local frontend escalation links configured"),
        (env.get("ORACLE_ESCALATION_TARGET_L1_ID") == "27362656", "L1 Group Inbox target = 27362656"),
        (env.get("ORACLE_ESCALATION_TARGET_L2_ID") == "27362656", "L2 Group Inbox target = 27362656"),
        (env.get("ORACLE_ESCALATION_TARGET_L3_ID") == "27362656", "L3 Group Inbox target = 27362656"),
        (env.get("ORACLE_ESCALATION_TARGET_L4_ID") == "27362656", "L4 Group Inbox target = 27362656"),
        (env.get("ORACLE_ESCALATION_FHIR_SENDER_MODE") == "smart_user", "FHIR sender = authenticated SMART user"),
        (env.get("ORACLE_ESCALATION_FHIR_ALLOW_SMART_USER_RECIPIENT_FALLBACK") == "true", "FHIR fallback enabled"),
        ("user/Communication.crus" in env.get("ORACLE_SCOPES", ""), "Oracle Communication write scope present"),
        ("user/Practitioner.rs" in env.get("ORACLE_SCOPES", ""), "Oracle Practitioner read scope present"),
        ("user/Person.rs" in env.get("ORACLE_SCOPES", ""), "Oracle Person read scope present"),
        (bool(env.get("SMTP_PASSWORD")), "SMTP password present (value not displayed)"),
        (bool(env.get("ORACLE_MESSAGING_SYSTEM_CLIENT_SECRET")), "Oracle System messaging secret present (value not displayed)"),
    ]

    try:
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        patients = mapping.get("patientsById") or {}
        scenarios: list[str] = []
        for plan in patients.values():
            if isinstance(plan, dict):
                for sid in plan.get("scenarioIds") or [plan.get("scenarioId")]:
                    if sid:
                        scenarios.append(str(sid))
        checks.append((len(patients) == 10, "Oracle patient map contains 10 sandbox patients"))
        checks.append((set(scenarios) == EXPECTED_SCENARIOS, "Oracle patient map covers all 14 CARDINAL scenarios"))
        checks.append((len(scenarios) == 14, "Each of the 14 scenarios is assigned exactly once"))
    except Exception as exc:
        checks.append((False, f"Oracle patient mapping validation failed: {exc}"))

    failures = 0
    for ok, label in checks:
        prefix = "[PASS]" if ok else "[FAIL]"
        print(prefix, label)
        if not ok:
            failures += 1

    print()
    print(f"FAIL={failures}")
    print("RESULT=" + ("PASS" if failures == 0 else "FAIL"))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
