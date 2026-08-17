from __future__ import annotations

import ast
import sys
from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in raw or raw.lstrip().startswith("#"):
            continue
        key, value = raw.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    paths = {
        "fhir": root / "backend/app/escalation/oracle/fhir_communication.py",
        "routes": root / "backend/app/escalation/routes.py",
        "adapter": root / "backend/app/escalation/oracle/adapter.py",
        "email": root / "backend/app/escalation/notifications/email_service.py",
        "frontend": root / "src/components/EscalationPage.jsx",
        "env": root / "backend/.env",
    }

    checks: list[tuple[bool, str]] = []
    for path in paths.values():
        checks.append((path.exists(), f"Required file exists: {path.relative_to(root)}"))

    for key in ("fhir", "routes", "adapter", "email"):
        try:
            ast.parse(paths[key].read_text(encoding="utf-8"))
            checks.append((True, f"Python syntax: {paths[key].relative_to(root)}"))
        except Exception as exc:
            checks.append((False, f"Python syntax: {paths[key].relative_to(root)} ({exc})"))

    fhir = paths["fhir"].read_text(encoding="utf-8")
    routes = paths["routes"].read_text(encoding="utf-8")
    adapter = paths["adapter"].read_text(encoding="utf-8")
    email = paths["email"].read_text(encoding="utf-8")
    frontend = paths["frontend"].read_text(encoding="utf-8")
    env = read_env(paths["env"])

    checks += [
        ("smart_practitioner_required_only" in fhir, "FHIR production profile uses SMART Practitioner"),
        ("authenticated_smart_practitioner" in fhir, "FHIR routing mode records authenticated SMART Practitioner"),
        ("configuredRecipient" not in fhir, "Old configured-recipient production field removed"),
        ("Test production Communication" in routes, "Diagnostic UI has one production Communication test"),
        ("Configured recipient" not in routes, "Old configured-recipient diagnostic buttons removed"),
        ('"verificationHttpStatus"' in adapter, "Adapter retains FHIR verification HTTP result"),
        ('"recipientName"' in adapter, "Adapter retains Oracle Group Inbox name"),
        ('"sentAt": now_iso()' in email, "Email service records sent timestamp"),
        ("Create HTTP:" in frontend, "Escalation page shows FHIR create HTTP"),
        ("Verified At:" in frontend, "Escalation page shows FHIR verification time"),
        ("ORACLE_FHIR_COMMUNICATION_VERIFIED" in frontend, "Timeline renders FHIR acronym consistently"),
        (env.get("ORACLE_ESCALATION_FHIR_RECIPIENT_MODE") == "smart_user", "Environment selects SMART Practitioner FHIR recipient mode"),
        (env.get("ORACLE_ESCALATION_FHIR_VERIFY_CREATED_RESOURCE") == "true", "FHIR create verification remains enabled"),
    ]

    failures = 0
    for ok, label in checks:
        print("[PASS]" if ok else "[FAIL]", label)
        if not ok:
            failures += 1

    print()
    print(f"FAIL={failures}")
    print("RESULT=" + ("PASS" if failures == 0 else "FAIL"))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
