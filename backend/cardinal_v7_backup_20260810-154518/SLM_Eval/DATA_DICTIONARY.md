# Data Dictionary — CARDINAL SLM Evaluation Episode Records

Schema version: `episode-slm-eval-v1`. All records are synthetic; PHI is fictional.
Every episode JSON in `episodes/` follows the structure below. Types: `str`, `int`, `float`, `bool`, `null`,
`array`, `object`.

## Top level
| Field | Type | Notes |
|---|---|---|
| `schemaVersion` | str | Always `"episode-slm-eval-v1"` |
| `episodeId` | str | Unique id, e.g. `VFIB-STEMI-001` |
| `incidentId` | str | `inc-<episodeId>` |
| `capturedAt` | str (ISO-8601) | Capture timestamp |
| `detectionSource` | str | `"CARDINAL continuous monitor (8-in-1 wearable)"` |
| `mode` | str | `"demo"` |
| `patient` | object | PHI block (below) |
| `episode` | object | Episode metadata (below) |
| `ecg` | object | 6-lead ECG waveform + measurements (below) |
| `ppg` | object | PPG waveform + SpO₂ (below) |
| `vitals` | object | Vital signs (below) |
| `labs` | object | Lab panel; each value is a lab object (below) |
| `clinicalContext` | object | `{ recentEvents: array<str> }` |

## `patient` (synthetic PHI)
| Field | Type | Notes |
|---|---|---|
| `disclaimer` | str | "SYNTHETIC / FICTIONAL PHI — demo only" |
| `name` | str | Fictional |
| `mrn` | str | `KG-######` |
| `dob` | str (YYYY-MM-DD) | |
| `age` | int | years |
| `sex` | str | `M` / `F` |
| `heightCm` | int | cm |
| `weightKg` | int | kg |
| `room` | str | Unit/bed |
| `admissionDate` | str (YYYY-MM-DD) | |
| `codeStatus` | str | e.g. `Full code`, `DNR/DNI` |
| `primaryDiagnosis` | str | |
| `history` | array<str> | Past medical history |
| `homeMedications` | array<str> | |
| `allergies` | array<str> | |
| `infusions` | array<str> | Active drips |

## `episode`
| Field | Type | Allowed / notes |
|---|---|---|
| `type` | str | machine key, e.g. `ventricular_fibrillation` |
| `display` | str | human label |
| `severity` | str | `critical` \| `warning` \| `info` |
| `state` | str | `CAPTURED` |
| `analysisStatus` | str | `pending` |
| `autoTriggered` | bool | |
| `triggerHeartRate` | int \| null | bpm at trigger (null if undetectable, e.g. VF) |
| `durationSeconds` | float | captured window length |

## `ecg`
| Field | Type | Notes |
|---|---|---|
| `leadNames` | array<str> | Exactly `["I","II","III","aVR","aVL","aVF"]` (6-lead) |
| `sampleRate` | int | Hz (250) |
| `durationSeconds` | float | seconds (8.0) |
| `gridPaperSpeedMmPerSec` | int | 25 |
| `gainMmPerMv` | int | 10 |
| `waveform` | object | `{ "<lead>": array<float> }`; length == `round(sampleRate*durationSeconds)`; units mV |
| `measurements` | object | Deterministic backend measurements — **the SLM's ECG input** (below) |

### `ecg.measurements`
| Field | Type | Notes |
|---|---|---|
| `rhythm` | str | Rhythm interpretation |
| `ventricularRateBpm` | int \| null | |
| `atrialRateBpm` | int \| null | |
| `regularity` | str | e.g. `regular`, `irregularly irregular`, `chaotic/irregular` |
| `qrsDurationMs` | int \| null | ms |
| `qtcMs` | int \| null | ms (may live in preEventNote if not measurable during event) |
| `prMs` | int \| null | ms |
| `axisDeg` | int \| null | degrees |
| `pWavePresent` | bool | |
| `stDeviationMm` | str | ST description |
| `morphology` | str | QRS/T morphology description |
| `ectopyPer10s` | int \| null | ectopic beats per 10 s |
| `preEventNote` | str | Pre-event baseline ECG context (e.g., prior QTc, ST elevation) |

## `ppg`
| Field | Type | Notes |
|---|---|---|
| `sampleRate` | int | Hz (125) |
| `durationSeconds` | float | seconds |
| `unit` | str | `a.u.` |
| `perfusionIndexPct` | float | |
| `signalQuality` | str | `good` \| `fair` \| `poor` \| `no_pulse` |
| `spo2Pct` | int \| null | % |
| `waveform` | array<float> | length == `round(sampleRate*durationSeconds)` |

## `vitals`
| Field | Type | Notes |
|---|---|---|
| `spo2Pct` | int \| null | % |
| `heartRateBpm` | int \| null | bpm |
| `respiratoryRateBpm` | int \| null | /min |
| `temperatureC` | float | °C |
| `bloodPressure` | object | `{ systolic:int\|null, diastolic:int\|null, map:int\|null, note:str }` (mmHg) |

## `labs`
Each lab entry is an object:
| Field | Type | Notes |
|---|---|---|
| `value` | float \| int | measured value |
| `unit` | str | e.g. `mmol/L`, `ng/mL`, `mg/dL` |
| `reference` | str | normal range |
| `flag` | str | `""`, `HIGH`, `LOW`, `CRITICAL HIGH`, `TOXIC`, etc. |

Lab keys are per-case and may include: `troponinT, ck_mb, potassium, magnesium, calcium, glucose, creatinine,
bun, lactate, ph, bicarbonate, wbc, procalcitonin, digoxinLevel, bnp, tsh, inr, hemoglobin`.

---

## `answer_key.json` (ground truth — NOT part of the SLM input)
| Field | Type | Notes |
|---|---|---|
| `schemaVersion` | str | `slm-eval-answerkey-v1` |
| `scoring` | object | rubric weights; must sum to 100 |
| `episodes` | object | keyed by episodeId |

Each `episodes.<id>`:
| Field | Type | Notes |
|---|---|---|
| `display` | str | |
| `primaryEtiology` | str | **the correct root cause** |
| `mechanism` | str | pathophysiology |
| `contributing` | array<str> | supporting factors |
| `mustIdentify` | array<str> | rhythm/finding the SLM must name |
| `mustRecommend` | array<str> | required safe actions |
| `distractors` | array<str> | red herrings the SLM must avoid |

### Scoring rubric (per episode, total 100)
`rhythm_identification` 25 · `primary_etiology` 30 · `contributing_factors` 20 · `recommended_actions` 20 · `avoids_distractors` 5.
