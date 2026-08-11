# CARDINAL V7.2 — 10-Patient Oracle Mapping + Clinical-Only Precomputed SLM Widget

This is a **full replacement patch** for the current project state. You do **not** need to apply V7.1 first.

## Runtime architecture

Oracle SMART authorization → authenticated Oracle Patient resource ID → V7 scenario mapping → API Range pre → controlled SLM_Eval episode → API Range post → V7 Etiology response → existing analytics page / SLM widget.

For the V7 evaluation path, the model input does not use Phase 6 deterministic output. In precomputed mode, no local Ollama inference is required.

## Oracle mapping

All 10 supplied Oracle test patients are mapped. Four patients have two candidate scenarios and six have one fixed scenario. All 14 scenarios occur exactly once across the ID map. See `ORACLE_V7_MAPPING.md`.

A dual mapping is selected once per SMART authorization and remains stable for that authorization. This prevents `/bootstrap`, `/start`, browser refreshes or React remounts from changing the episode halfway through a demo.

## SLM widget policy

The clinician-facing widget shows only the reasoning that is useful at the point of care/demo review:

- Identified Rhythm
- Primary Etiology
- Mechanism
- Key ECG Evidence
- Contributing Factors
- Rejected Alternatives
- Recommended Actions
- Uncertainty

The widget intentionally does **not** render model/profile name, scenario ID, precomputed/live status, JSON contract status, Phase 6 status, run number, output tokens, generation time, Response Provenance or Generation Record.

The V7 `episodeSummary` is retained in the backend contract but is not rendered because a precomputed scenario summary can contain the synthetic benchmark patient identity, which should not be visually mixed with the authenticated Oracle SMART patient shown by the page.

Technical metadata can remain available in backend/debug artifacts without appearing in the clinician-facing widget.

## Precomputed backend environment

```env
EVALUATION_INJECTION_ALLOWED_SCENARIOS=VFIB-STEMI-001,TORSADES-LQT-002,VT-ISCHEMIC-003,AFIB-RVR-SEPSIS-004,CHB-HYPERK-005,BRADY-DIGTOX-006,SVT-PSVT-007,NSVT-ECTOPY-008,WCT-DIFF-009,WPW-AFIB-010,FLUTTER-IC-011,PERI-STEMI-012,BRASH-013,AMIO-DDI-014

ETIOLOGY_V7_PRECOMPUTED_ENABLED=true
ETIOLOGY_V7_PRECOMPUTED_REQUIRED=true
ETIOLOGY_V7_PRECOMPUTED_ROOT=data/etiology_v7_precomputed
ETIOLOGY_V7_PRECOMPUTED_PROFILE=google-gemma-4-E2B-it

ETIOLOGY_V7_LIVE_MODEL_ENABLED=false
SLM_EVAL_ALLOW_MODEL=false
SLM_PHASE6_CONTEXT_ENABLED=false
```

Change only `ETIOLOGY_V7_PRECOMPUTED_PROFILE` when you want the deployed/local demo to present a different stored model run.

## Apply to your project

Project root:

```text
C:\Users\adity\Downloads\588_\7 Waveform
```

After extracting this patch:

```powershell
PowerShell -ExecutionPolicy Bypass -File `
  "<EXTRACTED_PATCH>\APPLY_CARDINAL_V7_2_FULLSTACK.ps1" `
  -ProjectRoot "C:\Users\adity\Downloads\588_\7 Waveform"
```

The installer creates a timestamped backup and does not overwrite `backend/.env` or `src/.env`.

Then verify:

```powershell
cd "C:\Users\adity\Downloads\588_\7 Waveform\backend"
python .\VERIFY_CARDINAL_V7_INTEGRATION.py
```

Expected summary:

```text
CARDINAL V7.2 verification: PASS
 - scenarios: 14
 - Oracle mapping: 10 IDs / 14 unique scenarios / 4 dual + 6 single
 - evaluation service: V7 direct path
 - Phase 6 deterministic context: not used by V7 model path
 - frontend: clinical-only V7 presentation; technical provenance hidden
```

## Important

The Oracle-to-scenario pairing is controlled demo/evaluation routing. It should not be described as a clinical inference from the Oracle patient's real conditions. The synthetic episode record remains the source of the V7 Etiology Engine evidence.
