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
DEFAULT_PROFILE = "google-gemma-4-E2B-it"

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
    waveform = ((record.get("ecg") or {}).get("waveform") or {})
    for lead in ("I", "II", "III", "aVR", "aVL", "aVF"):
        if not isinstance(waveform.get(lead), list) or not waveform[lead]:
            errors.append(f"{scenario_id}: missing waveform lead {lead}")

profile_root = ROOT / "data/etiology_v7_precomputed" / DEFAULT_PROFILE
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

mapping = json.loads(
    (ROOT / "app/evaluation_demo/patient_scenario_map.json").read_text(encoding="utf-8")
)
by_id = mapping.get("patientsById") or {}
if len(by_id) != 10:
    errors.append(f"Expected 10 Oracle patient IDs, found {len(by_id)}")
assigned = [
    scenario_id
    for plan in by_id.values()
    for scenario_id in (plan.get("scenarioIds") or [])
]
if len(assigned) != 14 or len(set(assigned)) != 14 or set(assigned) != set(EXPECTED):
    errors.append("Oracle patient mapping does not cover all 14 scenarios exactly once")
if sum(len(plan.get("scenarioIds") or []) == 2 for plan in by_id.values()) != 4:
    errors.append("Oracle mapping must contain four dual-scenario patient IDs")
if sum(len(plan.get("scenarioIds") or []) == 1 for plan in by_id.values()) != 6:
    errors.append("Oracle mapping must contain six single-scenario patient IDs")

service = (ROOT / "app/evaluation_injection/service.py").read_text(encoding="utf-8")
if "run_etiology_v7(" not in service:
    errors.append("Evaluation service does not call run_etiology_v7")
if "phase7_orchestrator.run_incident" in service:
    errors.append("Legacy phase7_orchestrator.run_incident is still present in evaluation service")
if "build_score_and_attach_cardinal(" in service:
    errors.append("Legacy cardinal bridge call is still present in evaluation service")

etiology = (ROOT / "app/evaluation_injection/etiology_v7.py").read_text(encoding="utf-8")
for required in (
    '"primaryEtiology": response.get("primaryEtiology")',
    '"recommendedActions": list(response.get("recommendedActions") or [])',
    '"responseMeta": {',
):
    if required not in etiology:
        errors.append(f"V7 native frontend field missing from etiology runtime: {required}")

assembler = (ROOT / "app/slm_widget/assembler.py").read_text(encoding="utf-8")
if '"deterministicOverlay": False' not in assembler:
    errors.append("V7 assembler direct path is missing")
if '"clinicalInterpretation": model_payload.get("modelResponse")' not in assembler:
    errors.append("V7 assembler is not returning the native clinicalInterpretation DTO")

episodes = (ROOT / "app/episodes.py").read_text(encoding="utf-8")
if 'metadata.get("mode") or "") != "evaluation_injection"' not in episodes:
    errors.append("Evaluation-specific Phase7 scheduling guard is missing")

frontend_files = [
    ROOT / "frontend/components/CriticalInterpretationWidget.jsx",
    ROOT / "frontend/components/CloudDemoAnalyticsAdditions.css",
    ROOT / "frontend/components/ClinicalPhysiologyPage.jsx",
    ROOT / "frontend/evaluation/evaluationWidgetAdapter.js",
]
for path in frontend_files:
    if not path.exists():
        errors.append(f"Missing frontend patch file: {path}")

critical = (ROOT / "frontend/components/CriticalInterpretationWidget.jsx").read_text(encoding="utf-8")
for label in (
    "Identified Rhythm",
    "Key ECG Evidence",
    "Primary Etiology",
    "Mechanism",
    "Rejected Alternatives",
    "Recommended Actions",
    "Uncertainty",
):
    if label not in critical:
        errors.append(f"Frontend V7 section missing: {label}")

for forbidden_label in (
    "Response Provenance",
    "Generation Record",
    "V7 JSON contract",
    "Phase 6 input",
    "Output tokens",
):
    if forbidden_label in critical:
        errors.append(f"Clinician-facing widget still renders technical metadata: {forbidden_label}")

if errors:
    print("CARDINAL V7.2 verification: FAILED")
    for error in errors:
        print(" -", error)
    raise SystemExit(1)

print("CARDINAL V7.2 verification: PASS")
print(f" - scenarios: {len(EXPECTED)}")
print(f" - default precomputed profile: {DEFAULT_PROFILE} (14/14 contract-valid)")
print(" - Oracle mapping: 10 IDs / 14 unique scenarios / 4 dual + 6 single")
print(" - evaluation service: V7 direct path")
print(" - Phase 6 deterministic context: not used by V7 model path")
print(" - frontend: clinical-only V7 presentation; technical provenance hidden")
