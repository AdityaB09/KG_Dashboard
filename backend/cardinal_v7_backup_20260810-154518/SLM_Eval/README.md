# CARDINAL SLM Evaluation Dataset — Episode Records for Clinical-Context & Etiology Testing

Synthetic, clinically-coherent episode records for evaluating whether the CARDINAL SLM produces **correct clinical documentation, clinical context, and etiology** when an episode is detected by continuous monitoring.

> ⚠️ **All patient identifiers and PHI are fictional.** These are demo/test records, not real patients.

## What's here
```
SLM_Eval/
├── episodes/                 # 8 SLM-INPUT records (observed data only — NO answer inside)
│   ├── VFIB-STEMI-001.json
│   ├── TORSADES-LQT-002.json
│   ├── VT-ISCHEMIC-003.json
│   ├── AFIB-RVR-SEPSIS-004.json
│   ├── CHB-HYPERK-005.json
│   ├── BRADY-DIGTOX-006.json
│   ├── SVT-PSVT-007.json
│   └── NSVT-ECTOPY-008.json
├── index.json                # manifest
├── answer_key.json           # GROUND TRUTH + grading rubric — DO NOT feed to the SLM
├── slm_prompt_template.md     # ready-to-use prompt for feeding an episode to the SLM
└── src/generate_episodes.py   # regenerate / extend the dataset
```

## Each record contains (6-lead ECG · PPG · SpO₂ · BP · Temp · labs · PHI)
| Block | Fields |
|---|---|
| `patient` (PHI) | name, mrn, dob, age, sex, height/weight/BMI, room, admissionDate, codeStatus, primaryDiagnosis, history[], homeMedications[], allergies[], infusions[] |
| `episode` | type, display, severity, state, analysisStatus, autoTriggered, triggerHeartRate, durationSeconds |
| `ecg` | leadNames (I, II, III, aVR, aVL, aVF), sampleRate 250 Hz, `waveform{}` (6 numeric arrays for display), gain/paper-speed, and **`measurements{}`** — rhythm, ventricular/atrial rate, regularity, QRS ms, QTc, PR, axis, ST deviation, P-wave presence, morphology, ectopy count, pre-event note |
| `ppg` | sampleRate, waveform, perfusionIndex, signalQuality, spo2Pct |
| `vitals` | spo2Pct, heartRateBpm, respiratoryRateBpm, temperatureC, bloodPressure{systolic, diastolic, map, note} |
| `labs` | targeted panel per case, each `{value, unit, reference, flag}` (e.g., troponin, K⁺, Mg, Ca, creatinine, lactate, WBC, procalcitonin, digoxin level, BNP…) |
| `clinicalContext` | recentEvents[] (the narrative the SLM must weave in) |

### Design principle (matches the dashboard)
The SLM should reason on **`ecg.measurements`** (deterministic backend measurements) + vitals + labs + PHI + context — **not** on the raw `waveform` arrays. The waveform arrays are included only so the ECG display widget renders; feeding raw samples to a language model for interpretation is explicitly out of scope (LLMs hallucinate on raw signals).

## The 8 episodes — each engineered around ONE data-supported etiology (with distractors)
| ID | Rhythm | Intended etiology (in answer key) | Discriminating signal |
|---|---|---|---|
| VFIB-STEMI-001 | Ventricular fibrillation | **Acute STEMI → VF arrest** | Troponin 1.85, CK-MB 48, anterior ST-elevation; K normal |
| TORSADES-LQT-002 | Polymorphic VT | **Acquired long-QT → Torsades** | QTc 618, K 2.9, Mg 1.2, sotalol+azithro+ondansetron |
| VT-ISCHEMIC-003 | Monomorphic VT | **Scar reentry (ischemic CM)** | EF 25%, AV dissociation/fusion, troponin only mildly ↑ |
| AFIB-RVR-SEPSIS-004 | AFib w/ RVR | **Sepsis-triggered new AFib** | Fever 38.9, WBC 18.4, PCT 6.2, lactate 3.1; no prior AFib |
| CHB-HYPERK-005 | Complete heart block | **Hyperkalemia (missed dialysis)** | K 7.4, peaked T, wide QRS, Cr 9.8, K-sparing meds |
| BRADY-DIGTOX-006 | Junctional bradycardia | **Digoxin toxicity + AKI** | Dig 3.8, AKI Cr 2.1, K 5.6, scooped ST, yellow-vision |
| SVT-PSVT-007 | Narrow-complex SVT | **AVNRT (benign)** | Young, recurrent, caffeine; normal labs/TSH/troponin |
| NSVT-ECTOPY-008 | PVCs + NSVT run | **Electrolyte ectopy (↓K/↓Mg)** | K 3.1, Mg 1.5 (thiazide, post-op); stable/non-sustained |

The set spans **shockable arrest, drug/electrolyte channelopathy, structural VT, sepsis-driven, conduction/metabolic, toxicology, benign SVT, and benign ectopy** — so it tests whether the SLM can *discriminate* etiologies rather than pattern-match a rhythm name. Several cases carry deliberate **distractors** (e.g., mildly elevated troponin that is demand-not-STEMI; hyperkalemia that is a marker, not the driver) to test specificity.

## How to run the evaluation
1. For each file in `episodes/`, feed the JSON to the SLM using `slm_prompt_template.md` (strip the `waveform` arrays if desired; keep `measurements`, vitals, labs, PHI, context).
2. Capture the SLM's output (documentation note + clinical context + etiology + recommendations).
3. Score against `answer_key.json` using the rubric below.

### Grading rubric (per episode, 100 pts)
| Dimension | Pts | Passing bar |
|---|---|---|
| Rhythm identification | 25 | Names the correct rhythm/arrhythmia |
| **Primary etiology** | 30 | States the correct root cause (not just the rhythm) |
| Contributing factors | 20 | Cites the supporting labs/history/meds |
| Recommended actions | 20 | Guideline-appropriate, safe next steps |
| Avoids distractors | 5 | Doesn't chase the planted red-herring |

A strong SLM should score ≥ 80 on the clear cases (VFIB-STEMI, TORSADES, CHB-HYPERK, AFIB-SEPSIS) and correctly avoid the distractors on the harder ones (VT-ISCHEMIC vs STEMI, BRADY-DIGTOX vs simple hyperkalemia).

## Notes
- `answer_key.json` must **never** be included in the SLM prompt.
- Extend the set by adding specs to `src/generate_episodes.py` and re-running.
- Schema is aligned to the dashboard's `episode-v2` shape (extended with vitals/labs/PHI) so records can also drive the analytics/display widgets, not just the SLM.
