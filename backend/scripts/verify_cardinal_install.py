from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

PASS = 0
WARN = 0
FAIL = 0


def passed(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"[PASS] {msg}")


def warned(msg: str) -> None:
    global WARN
    WARN += 1
    print(f"[WARN] {msg}")


def failed(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"[FAIL] {msg}")


def dotenv_last_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


def require_file(root: Path, rel: str) -> None:
    p = root / rel
    if p.is_file():
        passed(f"File present: {rel}")
    else:
        failed(f"Missing file: {rel}")


def require_text(root: Path, rel: str, text: str, label: str) -> None:
    p = root / rel
    if not p.exists():
        failed(f"{label}: missing {rel}")
        return
    content = p.read_text(encoding="utf-8", errors="replace")
    if text in content:
        passed(label)
    else:
        failed(f"{label}: expected text not found: {text}")


def check_env(values: dict[str, str], key: str, expected: str, label: str) -> None:
    actual = values.get(key)
    if actual == expected:
        passed(f"{label} ({key}={expected})")
    else:
        failed(f"{label}: final {key}={actual!r}, expected {expected!r}")


def configured(values: dict[str, str], key: str) -> bool:
    value = values.get(key, "").strip()
    if not value:
        return False
    return not any(token in value for token in ("PASTE_YOUR_", "REPLACE_WITH_", "<", ">"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[2]))
    args = parser.parse_args()
    root = Path(args.project_root).resolve()

    print("CARDINAL installation verifier (stdlib-only)")
    print(f"Project: {root}")
    print("=" * 72)

    for rel in (
        "package.json",
        "src/App.jsx",
        "backend/main.py",
        "backend/app/escalation/orchestrator.py",
        "backend/app/escalation/routes.py",
        "backend/app/escalation/policy_engine.py",
        "backend/app/escalation/policies/cardinal_hospital_v1.json",
        "backend/app/escalation/oracle/adapter.py",
        "backend/app/escalation/epic/adapter.py",
        "backend/app/escalation/notifications/email_service.py",
        "src/components/EscalationPage.jsx",
        "src/components/EscalationStatusCard.jsx",
        "src/services/escalationService.js",
    ):
        require_file(root, rel)

    require_text(root, "backend/app/evaluation_injection/service.py", "evaluate_and_dispatch", "Escalation orchestrator is invoked after evaluation")
    require_text(root, "backend/app/evaluation_injection/etiology_v7.py", "escalationRecommendation", "V7.1 escalation field exists")
    require_text(root, "src/main.jsx", "EscalationPage", "Escalation deep-link page is registered")
    require_text(root, "src/components/ClinicalPhysiologyPage.jsx", "EscalationStatusCard", "Analytics escalation card is registered")
    require_text(root, "vite.config.js", "envDir", "Vite envDir override exists")

    backend = dotenv_last_values(root / "backend/.env")
    frontend = dotenv_last_values(root / "src/.env")
    if not backend:
        failed("backend/.env could not be read")
    else:
        checks = {
            "ESCALATION_ENABLED": "true",
            "ESCALATION_POLICY_ID": "CARDINAL-HOSPITAL-V1",
            "ESCALATION_POLICY_PATH": "app/escalation/policies/cardinal_hospital_v1.json",
            "ESCALATION_EMAIL_ENABLED": "true",
            "ESCALATION_EMAIL_MODE": "smtp",
            "ESCALATION_EMAIL_L1": "aditya.bagayatkar09@gmail.com",
            "ESCALATION_EMAIL_L2": "aditya.bagayatkar09@gmail.com",
            "ESCALATION_EMAIL_L3": "aditya.bagayatkar09@gmail.com",
            "ESCALATION_EMAIL_L4": "aditya.bagayatkar09@gmail.com",
            "ETIOLOGY_V7_PRECOMPUTED_ENABLED": "false",
            "ETIOLOGY_V7_LIVE_MODEL_ENABLED": "true",
            "SLM_EVAL_ALLOW_MODEL": "true",
            "SLM_BASE_URL": "https://generativelanguage.googleapis.com/v1beta/openai",
            "SLM_MODEL": "gemini-3.6-flash",
            "SLM_REASONING_EFFORT": "low",
        }
        for key, expected in checks.items():
            check_env(backend, key, expected, "Backend env final override")
        if configured(backend, "SLM_API_KEY"):
            passed("Gemini API key configured")
        else:
            warned("Gemini API key still needs to be filled")
        if configured(backend, "SMTP_PASSWORD"):
            passed("Gmail App Password configured")
        else:
            warned("Gmail App Password still needs to be filled")
        if configured(backend, "ORACLE_MILLENNIUM_API_BASE_URL") and configured(backend, "ORACLE_ESCALATION_MESSAGE_SENDER_ID"):
            passed("Oracle native messaging base + sender configured")
        else:
            warned("Oracle native Group Inbox messaging is optional and not fully configured yet")
        if configured(backend, "EPIC_CDS_PUBLIC_BASE_URL"):
            passed("Epic public CDS base configured")
        else:
            warned("Epic local SMART is usable; public server-to-server CDS URL is not configured")

    if not frontend:
        failed("src/.env could not be read")
    else:
        check_env(frontend, "VITE_ESCALATION_POLL_MS", "2000", "Frontend escalation polling")
        check_env(frontend, "VITE_BACKEND_URL", "http://127.0.0.1:8000", "Existing frontend backend URL preserved")
        check_env(frontend, "VITE_API_BASE_URL", "http://127.0.0.1:8000", "Existing frontend API URL preserved")

    policy_path = root / "backend/app/escalation/policies/cardinal_hospital_v1.json"
    if policy_path.exists():
        try:
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            if policy.get("policyId") == "CARDINAL-HOSPITAL-V1":
                passed("Hospital policy JSON loads with correct policyId")
            else:
                failed(f"Unexpected policyId: {policy.get('policyId')!r}")
            if policy.get("version"):
                passed(f"Hospital policy version present: {policy['version']}")
            else:
                failed("Hospital policy version missing")
        except Exception as exc:
            failed(f"Hospital policy JSON invalid: {exc}")

    manifest_path = root / "CARDINAL_UPDATE_MANIFEST.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            mismatches = 0
            files = manifest.get("files", {})
            for rel, expected in files.items():
                p = root / Path(rel)
                if not p.exists():
                    failed(f"Manifest file missing: {rel}")
                    mismatches += 1
                    continue
                if sha256(p).lower() != str(expected).lower():
                    failed(f"Manifest hash mismatch: {rel}")
                    mismatches += 1
            if not mismatches:
                passed(f"All {len(files)} installed payload hashes match")
        except Exception as exc:
            failed(f"Manifest validation failed: {exc}")
    else:
        warned("CARDINAL_UPDATE_MANIFEST.json missing; hash verification skipped")

    print("=" * 72)
    print(f"PASS={PASS} WARN={WARN} FAIL={FAIL}")
    if FAIL:
        print("INSTALLATION RESULT: FAIL")
        return 1
    print("INSTALLATION RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
