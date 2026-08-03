# MANIFEST — CARDINAL SLM Evaluation Dataset v1

Package of synthetic CARDINAL episode records for evaluating SLM clinical documentation, clinical context, and etiology reasoning. All PHI is fictional.

- Episodes: **8** | Schema: `episode-slm-eval-v1` | Answer key: `slm-eval-answerkey-v1`
- ECG: 6-lead (I, II, III, aVR, aVL, aVF) @250 Hz, 8 s + deterministic measurements
- Signals: PPG/SpO2, blood pressure, temperature, respiratory rate
- Labs: targeted panel per case; full synthetic PHI + history/meds/context
- Tests: 188 pytest checks (schema, waveforms, clinical ranges, answer key, no-answer-leak)

## Contents (SHA-256, first 16 hex)

| File | Bytes | SHA-256 |
|---|---:|---|
| `DATA_DICTIONARY.md` | 5,398 | `7f5849bdb5d18292` |
| `README.md` | 5,713 | `d5818394ff8765ee` |
| `answer_key.json` | 8,829 | `2f2ab0671ff95573` |
| `episodes/AFIB-RVR-SEPSIS-004.json` | 210,608 | `8dfc1d0932119b0b` |
| `episodes/BRADY-DIGTOX-006.json` | 210,615 | `fae5b8dab8aa9d76` |
| `episodes/CHB-HYPERK-005.json` | 212,139 | `7e1be6c3f14afff5` |
| `episodes/NSVT-ECTOPY-008.json` | 211,262 | `87c56a0029675967` |
| `episodes/SVT-PSVT-007.json` | 209,817 | `fad81104af714ef5` |
| `episodes/TORSADES-LQT-002.json` | 214,482 | `e250ed106543a375` |
| `episodes/VFIB-STEMI-001.json` | 215,065 | `f1dec7634eb8aea6` |
| `episodes/VT-ISCHEMIC-003.json` | 216,736 | `f30ae02966ac914c` |
| `index.json` | 1,964 | `a0f85d07715ab1c1` |
| `pytest.ini` | 76 | `76a1c436aa616661` |
| `requirements.txt` | 127 | `e340899329f13c34` |
| `slm_prompt_template.md` | 1,978 | `4292896f30c7f9ac` |
| `src/generate_episodes.py` | 27,210 | `c1c60b7fd3951891` |
| `tests/README.md` | 1,064 | `f0fb3014574e4410` |
| `tests/conftest.py` | 925 | `882c411ede59d93f` |
| `tests/test_answer_key.py` | 1,444 | `fff8d237fc50313c` |
| `tests/test_clinical_ranges.py` | 1,927 | `7b464bc83367d35b` |
| `tests/test_no_answer_leak.py` | 1,379 | `909f84541f5b091d` |
| `tests/test_schema.py` | 2,724 | `1125f40be554d34d` |
| `tests/test_waveforms.py` | 1,487 | `2da7e29e992a537e` |

## Episodes

| ID | Display | Severity | Patient |
|---|---|---|---|
| VFIB-STEMI-001 | Ventricular fibrillation | critical | Robert D. Hale |
| TORSADES-LQT-002 | Polymorphic VT (Torsades de pointes) | critical | Margaret A. Sullivan |
| VT-ISCHEMIC-003 | Monomorphic ventricular tachycardia | critical | James P. Whitfield |
| AFIB-RVR-SEPSIS-004 | Atrial fibrillation with rapid ventricular response | warning | Dorothy E. Chen |
| CHB-HYPERK-005 | Complete heart block with bradycardia | critical | Walter J. Osei |
| BRADY-DIGTOX-006 | Junctional bradycardia (digoxin effect) | warning | Eleanor R. Vance |
| SVT-PSVT-007 | Supraventricular tachycardia (regular narrow-complex) | warning | Aisha N. Rahman |
| NSVT-ECTOPY-008 | Frequent PVCs with non-sustained VT run | warning | Thomas B. Nguyen |
