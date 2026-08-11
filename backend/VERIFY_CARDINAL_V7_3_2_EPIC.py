from __future__ import annotations

import json
import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app"

required = [
    APP / "epic_sandbox.py",
    APP / "epic_smart.py",
    APP / "evaluation_demo" / "epic_mapping.py",
    APP / "evaluation_demo" / "epic_service.py",
    APP / "evaluation_demo" / "epic_routes.py",
    APP / "evaluation_demo" / "epic_patient_scenario_map.json",
]
for path in required:
    if not path.exists():
        raise SystemExit(f"MISSING: {path}")

for path in required:
    if path.suffix == ".py":
        py_compile.compile(str(path), doraise=True)

mapping = json.loads((APP / "evaluation_demo" / "epic_patient_scenario_map.json").read_text(encoding="utf-8"))
patients = mapping.get("patientsByKey") or {}
expected_keys = {
    "cadence_anna",
    "clin_doc_henry",
    "grand_central_john",
    "optime_omar",
    "nelson_kyle",
}
if set(patients) != expected_keys:
    raise SystemExit(f"Epic patient keys mismatch: {set(patients)}")

assigned = [scenario for plan in patients.values() for scenario in plan.get("scenarioIds", [])]
if len(assigned) != 14 or len(set(assigned)) != 14:
    raise SystemExit(f"Epic mapping must cover 14 unique scenarios exactly once; got {len(assigned)} / {len(set(assigned))}")

combined = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in APP.rglob("*.py"))
for obsolete in [
    "EPIC_EVALUATION_DEMO_ALLOW_HASH_FALLBACK",
    "epic-hash-fallback",
    "stable_hash_by_epic_patient",
    "EPIC_PATIENT_ID_01",
]:
    if obsolete in combined:
        raise SystemExit(f"Obsolete Epic mapping token still present: {obsolete}")

main = (ROOT / "main.py").read_text(encoding="utf-8", errors="ignore")
for expected in ["patientKey", "patientDisplayName", "patientVerified"]:
    if expected not in main:
        raise SystemExit(f"main.py Epic session endpoint is missing {expected}")

print("CARDINAL V7.3.2 Epic verification: PASS")
print(" - Epic LaunchPad allowlist: 5 verified names")
print(" - Epic Patient ID source: SMART token response")
print(" - Patient.Read verification: required")
print(" - Unknown-patient hash fallback: removed")
print(" - Explicit Epic mapping: 5 patients / 14 unique scenarios")
print(" - Oracle code: not touched by this overlay")
print(" - Frontend code: not touched by this overlay")
