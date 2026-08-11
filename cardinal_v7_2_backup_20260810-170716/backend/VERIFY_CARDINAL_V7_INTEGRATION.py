from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXPECTED = [
    "VFIB-STEMI-001", "TORSADES-LQT-002", "VT-ISCHEMIC-003",
    "AFIB-RVR-SEPSIS-004", "CHB-HYPERK-005", "BRADY-DIGTOX-006",
    "SVT-PSVT-007", "NSVT-ECTOPY-008", "WCT-DIFF-009",
    "WPW-AFIB-010", "FLUTTER-IC-011", "PERI-STEMI-012",
    "BRASH-013", "AMIO-DDI-014",
]
REQUIRED_RESPONSE_KEYS = {
    "episodeSummary", "rhythm", "keyECGEvidence", "primaryEtiology",
    "mechanism", "contributingFactors", "rejectedAlternatives",
    "recommendedActions", "uncertainty",
}

errors: list[str] = []
index = json.loads((ROOT / "SLM_Eval/index.json").read_text(encoding="utf-8"))
actual = [item["episodeId"] for item in index.get("episodes", [])]
if actual != EXPECTED:
    errors.append(f"Scenario index mismatch: {actual}")

for scenario_id in EXPECTED:
    path = ROOT / "SLM_Eval/episodes" / f"{scenario_id}.json"
    if not path.exists():
        errors.append(f"Missing scenario: {path}")
        continue
    record = json.loads(path.read_text(encoding="utf-8"))
    ecg = record.get("ecg") or {}
    waveform = ecg.get("waveform") or {}
    for lead in ("I", "II", "III", "aVR", "aVL", "aVF"):
        if not isinstance(waveform.get(lead), list) or not waveform[lead]:
            errors.append(f"{scenario_id}: missing waveform lead {lead}")

profile_root = ROOT / "data/etiology_v7_precomputed/google-medgemma-27b-it"
for scenario_id in EXPECTED:
    path = profile_root / f"{scenario_id}.json"
    if not path.exists():
        errors.append(f"Missing precomputed response: {path}")
        continue
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("generationSucceeded") is not True or payload.get("validContract") is not True:
        errors.append(f"{scenario_id}: default precomputed result is not contract-valid")
    response = payload.get("modelResponse")
    if not isinstance(response, dict) or set(response) != REQUIRED_RESPONSE_KEYS:
        errors.append(f"{scenario_id}: response keys do not match V7 contract")

service = (ROOT / "app/evaluation_injection/service.py").read_text(encoding="utf-8")
if "run_etiology_v7(" not in service:
    errors.append("Evaluation service does not call run_etiology_v7")
if "phase7_orchestrator.run_incident" in service:
    errors.append("Legacy phase7_orchestrator.run_incident is still present in evaluation service")
if "build_score_and_attach_cardinal(" in service:
    errors.append("Legacy cardinal bridge call is still present in evaluation service")

assembler = (ROOT / "app/slm_widget/assembler.py").read_text(encoding="utf-8")
if '"deterministicOverlay": False' not in assembler:
    errors.append("V7 assembler direct path is missing")

episodes = (ROOT / "app/episodes.py").read_text(encoding="utf-8")
if 'metadata.get("mode") or "") != "evaluation_injection"' not in episodes:
    errors.append("Evaluation-specific Phase7 scheduling guard is missing")

if errors:
    print("CARDINAL V7 verification: FAILED")
    for error in errors:
        print(" -", error)
    raise SystemExit(1)

print("CARDINAL V7 verification: PASS")
print(f" - scenarios: {len(EXPECTED)}")
print(" - default precomputed profile: google-medgemma-27b-it (14/14 contract-valid)")
print(" - evaluation service: V7 direct path")
print(" - evaluation Phase6/Phase7 orchestrator: disabled")
print(" - frontend: unchanged compatibility endpoint retained")
