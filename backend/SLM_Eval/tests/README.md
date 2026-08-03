# Test suite

Validates the dataset's integrity and evaluation-fairness. Run from the package root:

```bash
pip install -r requirements.txt
pytest            # or: python3 -m pytest tests -q
```

## What is covered (188 checks across 8 episodes)
- **test_schema.py** — every record has the required blocks/fields (patient, episode, ecg+measurements, ppg, vitals, labs, context) with correct types and enums.
- **test_waveforms.py** — exactly the 6 leads (I, II, III, aVR, aVL, aVF); ECG/PPG sample counts equal sampleRate×duration; numeric samples; plausible ECG amplitude (<6 mV).
- **test_clinical_ranges.py** — vitals within physiologic bounds (nulls allowed for arrest), systolic≥diastolic, PPG/vitals SpO2 agree, labs numeric with units (pH/INR unitless).
- **test_answer_key.py** — every episode has a complete ground-truth entry; rubric weights sum to 100; index matches files.
- **test_no_answer_leak.py** — SLM-input files contain NO ground-truth/answer fields and no stated diagnosis; PHI flagged synthetic. (Guarantees a fair evaluation.)
