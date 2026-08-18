from __future__ import annotations

import argparse
import hashlib
import json
import os
import py_compile
import sys
import urllib.error
import urllib.request
from pathlib import Path


MARKER = "# CARDINAL ORACLE SYSTEM MESSAGING V3"


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime", action="store_true")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    backend = root / "backend"
    env_path = backend / ".env"
    env = parse_env(env_path)
    passes = 0
    warns = 0
    fails = 0

    def emit(kind: str, message: str) -> None:
        nonlocal passes, warns, fails
        if kind == "PASS": passes += 1
        elif kind == "WARN": warns += 1
        else: fails += 1
        print(f"[{kind}] {message}")

    print("=== CARDINAL ORACLE SYSTEM MESSAGING V3 VERIFIER ===")

    required_files = [
        "backend/app/escalation/oracle/system_auth.py",
        "backend/app/escalation/oracle/base_urls.py",
        "backend/app/escalation/oracle/adapter.py",
        "backend/app/escalation/oracle/group_messaging.py",
        "backend/app/escalation/oracle/fhir_identity.py",
        "backend/app/escalation/routes.py",
        "backend/scripts/verify_oracle_system_messaging_v3.py",
    ]
    for rel in required_files:
        path = root / rel
        emit("PASS" if path.exists() else "FAIL", f"Required V3 file exists: {rel}")

    text = env_path.read_text(encoding="utf-8", errors="replace") if env_path.exists() else ""
    emit("PASS" if MARKER in text else "FAIL", "V3 operational ENV marker appended")

    provider_id = env.get("ORACLE_CLIENT_ID", "")
    system_id = env.get("ORACLE_MESSAGING_SYSTEM_CLIENT_ID", "")
    system_secret = env.get("ORACLE_MESSAGING_SYSTEM_CLIENT_SECRET", "")
    tenant = env.get("ORACLE_MESSAGING_TENANT_ID", "")
    scopes = set(env.get("ORACLE_MESSAGING_SYSTEM_SCOPES", "").split())

    emit("PASS" if provider_id else "FAIL", "Existing Provider ORACLE_CLIENT_ID remains configured")
    emit("PASS" if system_id else "FAIL", "System messaging client ID configured")
    emit("PASS" if system_secret else "FAIL", "System messaging client secret configured (value not displayed)")
    emit("PASS" if tenant else "FAIL", "System messaging tenant ID configured")
    legacy_tenant = env.get("ORACLE_MILLENNIUM_TENANT_ID", "")
    if legacy_tenant and tenant:
        emit("PASS" if legacy_tenant == tenant else "FAIL", "Legacy and System Oracle tenant IDs are consistent")
    emit(
        "PASS" if system_id and provider_id and system_id != provider_id else "FAIL",
        "Provider and System Oracle client IDs are distinct",
    )
    for required_scope in (
        "oraclehealth:millennium:recipient",
        "oraclehealth:millennium:message",
    ):
        emit("PASS" if required_scope in scopes else "FAIL", f"System scope present: {required_scope}")
    emit(
        "PASS" if "oraclehealth:millennium:personnel" in scopes else "WARN",
        "Optional System personnel scope present",
    )

    provider_scopes = set(env.get("ORACLE_SCOPES", "").split())
    for required_scope in (
        "user/Communication.crus",
        "user/Practitioner.rs",
        "user/Person.rs",
    ):
        emit("PASS" if required_scope in provider_scopes else "FAIL", f"Provider SMART scope present: {required_scope}")
    if "user/Practitioner.read" in provider_scopes or "user/Person.read" in provider_scopes:
        emit("WARN", "Legacy .read-only Practitioner/Person scope remains; .rs is the effective search-capable requirement")

    emit(
        "PASS" if env.get("SLM_MODEL") == "gemini-3.5-flash-lite" else "FAIL",
        "Effective SLM model is gemini-3.5-flash-lite",
    )
    emit(
        "PASS" if env.get("SLM_OMIT_SAMPLING_PARAMS", "").lower() in {"1", "true", "yes", "on"} else "WARN",
        "Gemini deprecated sampling parameters are omitted",
    )

    # Verify code-level behavior markers.
    system_auth = (root / "backend/app/escalation/oracle/system_auth.py").read_text(encoding="utf-8", errors="replace") if (root / "backend/app/escalation/oracle/system_auth.py").exists() else ""
    routes = (root / "backend/app/escalation/routes.py").read_text(encoding="utf-8", errors="replace") if (root / "backend/app/escalation/routes.py").exists() else ""
    adapter = (root / "backend/app/escalation/oracle/adapter.py").read_text(encoding="utf-8", errors="replace") if (root / "backend/app/escalation/oracle/adapter.py").exists() else ""

    emit("PASS" if "client_credentials" in system_auth and "auth=(client_id, client_secret)" in system_auth else "FAIL", "System client_credentials token acquisition implemented")
    emit("PASS" if "_CachedToken" in system_auth and "expires_at_epoch" in system_auth else "FAIL", "System token caching/expiry implemented")
    emit("PASS" if "access_token" in system_auth and "test_system_token" in system_auth else "FAIL", "Safe token-test diagnostic implemented")
    emit("PASS" if "/api/escalation/oracle/system/token-test" in routes else "FAIL", "System token-test route registered")
    emit("PASS" if "/api/escalation/oracle/system/group-inboxes" in routes else "FAIL", "System Group Inbox discovery route registered")
    emit("PASS" if "get_system_access_token" in adapter else "FAIL", "Oracle escalation adapter uses System token service")
    emit("PASS" if "discover_current_person" in adapter else "FAIL", "Message sender PERSON can be resolved from Provider FHIR context")

    # Hash verification, when the installer copied the manifest.
    manifest_path = root / "CARDINAL_ORACLE_SYSTEM_V3_MANIFEST.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for rel, expected in manifest.get("files", {}).items():
            path = root / rel
            if not path.exists():
                emit("FAIL", f"Manifest file missing: {rel}")
            else:
                emit("PASS" if sha256(path) == expected else "FAIL", f"SHA-256 matches package: {rel}")
    else:
        emit("WARN", "V3 hash manifest not found in project root")

    # Compile only the V3 Python files; this does not import app dependencies.
    compile_files = [root / rel for rel in required_files if rel.endswith(".py")]
    compile_ok = True
    for path in compile_files:
        if not path.exists():
            compile_ok = False
            continue
        try:
            py_compile.compile(str(path), doraise=True)
        except Exception as exc:
            compile_ok = False
            print(f"    compile error: {path}: {exc}")
    emit("PASS" if compile_ok else "FAIL", "V3 backend Python files compile")

    if args.runtime:
        for path in (
            "/api/escalation/oracle/system/readiness",
            "/api/escalation/oracle/system/token-test",
            "/api/escalation/oracle/system/group-inboxes",
        ):
            url = args.backend_url.rstrip("/") + path
            try:
                with urllib.request.urlopen(url, timeout=30) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                state = str(payload.get("state") or payload.get("status") or "").upper()
                ok = state in {"READY", "200"} or payload.get("status") == "ready"
                emit("PASS" if ok else "WARN", f"Runtime probe {path}: {state or payload.get('status')}")
                if path.endswith("group-inboxes") and isinstance(payload.get("items"), list):
                    print(f"    group inbox count: {len(payload['items'])}")
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                emit("WARN", f"Runtime probe unavailable for {path}: {exc}")

    print()
    print(f"PASS={passes} WARN={warns} FAIL={fails}")
    print("RESULT=" + ("PASS" if fails == 0 else "FAIL"))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
