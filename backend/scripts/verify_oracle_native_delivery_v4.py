from __future__ import annotations

import argparse
import hashlib
import json
import py_compile
import sys
from pathlib import Path

MARKER = "# CARDINAL ORACLE NATIVE DELIVERY V4"

EXPECTED_HASHES = {}


def env_last(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    backend = root / "backend"
    env = env_last(backend / ".env")

    passed = warned = failed = 0

    def check(ok: bool, text: str, warn: bool = False):
        nonlocal passed, warned, failed
        if ok:
            passed += 1
            print(f"[PASS] {text}")
        elif warn:
            warned += 1
            print(f"[WARN] {text}")
        else:
            failed += 1
            print(f"[FAIL] {text}")

    print("=== CARDINAL ORACLE NATIVE DELIVERY V4 VERIFIER ===")
    required = [
        "backend/app/escalation/oracle/fhir_user.py",
        "backend/app/escalation/oracle/fhir_communication.py",
        "backend/app/escalation/oracle/group_messaging.py",
        "backend/app/escalation/routes.py",
        "backend/tests/test_oracle_native_delivery_v4.py",
        "backend/scripts/verify_oracle_native_delivery_v4.py",
        "VERIFY_ORACLE_NATIVE_DELIVERY_V4.ps1",
    ]
    for rel in required:
        check((root / rel).exists(), f"Required V4 file exists: {rel}")

    check(MARKER in (backend / ".env").read_text(encoding="utf-8", errors="replace"), "V4 ENV marker appended")
    check(env.get("ORACLE_ESCALATION_FHIR_SENDER_MODE") == "smart_user", "FHIR Communication sender mode is smart_user")
    check(env.get("ORACLE_ESCALATION_FHIR_INCLUDE_ENCOUNTER", "false").lower() == "false", "FHIR minimal payload omits optional encounter by default")
    check("user/Communication.crus" in env.get("ORACLE_SCOPES", ""), "Provider Communication scope remains present")
    check("user/Practitioner.rs" in env.get("ORACLE_SCOPES", ""), "Provider Practitioner search scope remains present")
    check("openid" in env.get("ORACLE_SCOPES", "") and "fhirUser" in env.get("ORACLE_SCOPES", ""), "Provider SMART requests openid + fhirUser")
    check(bool(env.get("ORACLE_MESSAGING_SYSTEM_CLIENT_ID")), "System messaging client ID remains configured")
    check(bool(env.get("ORACLE_MESSAGING_SYSTEM_CLIENT_SECRET")), "System messaging client secret remains configured (value not displayed)")
    check("oraclehealth:millennium:recipient" in env.get("ORACLE_MESSAGING_SYSTEM_SCOPES", ""), "System recipient scope remains configured")
    check("oraclehealth:millennium:message" in env.get("ORACLE_MESSAGING_SYSTEM_SCOPES", ""), "System message scope remains configured")
    check(bool(env.get("ORACLE_ESCALATION_TARGET_L2_ID")), "Oracle Group Inbox target is configured", warn=True)
    check(bool(env.get("ORACLE_ESCALATION_FHIR_RECIPIENT_REFERENCE_L2")), "Oracle FHIR recipient is configured", warn=True)

    fhir_user = (backend / "app/escalation/oracle/fhir_user.py").read_text(encoding="utf-8")
    fhir_comm = (backend / "app/escalation/oracle/fhir_communication.py").read_text(encoding="utf-8")
    msg = (backend / "app/escalation/oracle/group_messaging.py").read_text(encoding="utf-8")
    routes = (backend / "app/escalation/routes.py").read_text(encoding="utf-8")
    # Behavioral/source-structure check. The implementation builds the source label
    # dynamically as f"id_token.{key}", so looking for the literal string
    # "id_token.fhirUser" creates a false negative even though fhirUser is supported.
    smart_fhir_user_ok = (
        "def smart_fhir_user_reference" in fhir_user
        and 'for key in ("fhirUser", "fhir_user", "profile")' in fhir_user
        and "_decode_jwt_payload" in fhir_user
        and "Practitioner/" in fhir_user
        and 'return ref, f"id_token.{key}"' in fhir_user
    )
    check(smart_fhir_user_ok, "SMART fhirUser Practitioner discovery implemented")
    check("resolve_smart_fhir_user" in fhir_comm, "FHIR Communication uses authenticated SMART user resolver")
    check('"contentType": "text/plain"' in fhir_comm, "FHIR Communication uses Oracle-documented text/plain content type")
    check("ORACLE_ESCALATION_FHIR_INCLUDE_ENCOUNTER" in fhir_comm, "FHIR Communication optional encounter guard implemented")
    check("XHTML 1.0 Transitional" in msg and "Unable to convert HTML to RTF" in msg, "Message Center XHTML/RTF hardening and retry implemented")
    check("<a " not in msg, "Message builder avoids anchor elements in converter-sensitive XHTML")
    check("/api/escalation/oracle/fhir/current-user" in routes, "Authenticated FHIR user diagnostic route registered")

    compile_targets = [
        backend / "app/escalation/oracle/fhir_user.py",
        backend / "app/escalation/oracle/fhir_communication.py",
        backend / "app/escalation/oracle/group_messaging.py",
        backend / "app/escalation/routes.py",
    ]
    compile_ok = True
    try:
        for target in compile_targets:
            py_compile.compile(str(target), doraise=True)
    except Exception as exc:
        compile_ok = False
        print(f"       compile error: {exc}")
    check(compile_ok, "V4 backend Python files compile")

    print(f"\nPASS={passed} WARN={warned} FAIL={failed}")
    print("RESULT=PASS" if failed == 0 else "RESULT=FAIL")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
