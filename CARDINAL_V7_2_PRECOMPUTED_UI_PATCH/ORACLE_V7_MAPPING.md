# CARDINAL V7.2 Oracle SMART → Scenario Mapping

All 10 Oracle SMART test patients supplied for this project are mapped by Patient resource ID. The 14 V7 scenarios are assigned exactly once across those 10 IDs: four patients have two candidate scenarios and six patients have one fixed scenario.

| Oracle patient | Patient ID | DOB | V7 scenario candidate(s) | Runtime behavior |
|---|---:|---|---|---|
| Wilma SMART | `12724065` | 1947-03-16 | VFIB-STEMI-001; PERI-STEMI-012 | Stable 50/50 per SMART authorization |
| Timmy SMART | `12724069` | 2012-02-19 | SVT-PSVT-007 | Fixed |
| Nancy SMART | `12724066` | 1980-08-11 | TORSADES-LQT-002 | Fixed |
| Joe SMART | `12724067` | 1976-04-29 | AFIB-RVR-SEPSIS-004; BRASH-013 | Stable 50/50 per SMART authorization |
| Hailey SMART | `12724068` | 2003-12-02 | FLUTTER-IC-011 | Fixed |
| Fredrick SMART | `12724070` | 1946-08-22 | CHB-HYPERK-005; WCT-DIFF-009 | Stable 50/50 per SMART authorization |
| Valerie SMART | `12724071` | 1984-04-15 | BRADY-DIGTOX-006; AMIO-DDI-014 | Stable 50/50 per SMART authorization |
| Sandy SMART | `12742399` | 2019-11-15 | WPW-AFIB-010 | Fixed |
| Baby Boy SMART | `12742397` | 2020-03-02 | NSVT-ECTOPY-008 | Fixed |
| Tim PETERS | `12742400` | 1970-01-02 | VT-ISCHEMIC-003 | Fixed |

## Runtime selection

`patientsById` is authoritative. If a patient has two candidates, `mapping.py` uses the random SMART authorization/session key as input to a SHA-256 selection. That produces one of the two candidates for a new SMART authorization and keeps the same choice across bootstrap/start calls, browser refreshes and React remounts inside that authorization. A single-scenario mapping always returns its fixed scenario.

This is controlled demo routing. The Oracle patient record does not clinically determine the synthetic SLM_Eval scenario.

## Why the widget does not show scenario/model provenance

The clinician-facing SLM widget is limited to clinical reasoning fields: identified rhythm, primary etiology, mechanism, ECG evidence, contributing factors, rejected alternatives, recommended actions and uncertainty. Model/profile, scenario ID, precomputed/live status, JSON-contract status, Phase 6 status, run number, token counts and generation timing are intentionally not rendered.

The V7 `episodeSummary` is also kept out of the widget because a precomputed scenario response may name the synthetic benchmark patient, while the page itself is showing the authenticated Oracle SMART patient.
