# README — Fix and Package the KGEN V6.0.3 Local Validator/Scorer

## Purpose

Give this README and the accompanying scripts to the current working thread.

This is a **validator/scorer-only correction** identified after auditing:

```text
KGEN_MEDGEMMA_RESULTS_V6_0_3_full_both_run_2.zip
```

The MedGemma outputs are already generated and valid. Do **not** rerun Lightning and do **not** change the V6.0.3 prompt, model input, four-field response contract, or saved model results for this task.

The goal is to correct the local validation/scoring implementation and create a small source-only ZIP that can be uploaded back to ChatGPT for independent revalidation and rescoring.

---

# 1. Problems that must be fixed

## 1.1 The validation/scoring CLI is not loading the real scenario answer keys

The current CLI contains logic equivalent to:

```python
answer_key = (
    evidence.get("answerKey")
    or evidence.get("scoringMetadata")
    or {}
)
```

The V6.0.3 `validator_evidence.json` files do not contain those fields. The scorer therefore receives `{}` and returns misleading fallback scores, including identical 95/100 results.

The corrected CLI must load the mandatory answer key through:

```python
from app.evaluation_injection.answer_key_loader import (
    load_scenario_answer_key,
)

answer_key = load_scenario_answer_key(
    scenario,
    allow_legacy_fallback=False,
)
```

No empty answer-key fallback is allowed.

The real answer keys are located at:

```text
backend/data/evaluation_answer_keys/<SCENARIO_ID>.json
```

for these eight scenarios:

```text
VFIB-STEMI-001
TORSADES-LQT-002
VT-ISCHEMIC-003
AFIB-RVR-SEPSIS-004
CHB-HYPERK-005
BRADY-DIGTOX-006
SVT-PSVT-007
NSVT-ECTOPY-008
```

## 1.2 Empty uncertainty partial credit is applied in the wrong order

The current scorer checks expected uncertainty concepts before applying the answer key's empty-array policy.

That causes this valid policy:

```json
{
  "emptyAllowed": true,
  "partialCreditForEmpty": true
}
```

to receive zero whenever the answer key also lists an acceptable uncertainty concept.

The corrected `_uncertainty_score()` must check the empty-array policy **before** concept matching:

```python
if not actual_items and policy.get("emptyAllowed", True):
    score = (
        round(maximum * 0.5)
        if policy.get("partialCreditForEmpty", True)
        else maximum
    )
    return score, [], [], expected, {}
```

This produces the documented partial credit while still reporting the optional concept as missing.

## 1.3 Semantic matching must remain boundary- and negation-safe

Retain and test these fixes:

- `regular rhythm` must not match inside `irregular rhythm`;
- `sustained ventricular tachycardia` must not match inside `non-sustained ventricular tachycardia`;
- locally negated phrases must not count as positive evidence;
- phrase matching must use token boundaries rather than raw substring matching.

## 1.4 Safe numeric rounding must remain accepted

Examples such as:

```text
model: 54 bpm
evidence: 54.098 bpm
```

must be treated as a safe display rounding, not an unsupported number.

The tolerance must remain narrow enough that materially different values are still rejected.

## 1.5 Technical Phase 6 conflicts must not be forced into the presentation

A measurement conflict should be required in the user-facing response only when the conflict is explicitly marked as etiologically/presentation material, using a field such as:

```text
etiologyMaterial
materialToEtiology
clinicallyMaterialToEtiology
presentationMaterial
```

A generic technical conflict must remain internal audit metadata.

---

# 2. Files that must be updated

At minimum:

```text
backend/app/evaluation_injection/response_validator.py
backend/app/evaluation_injection/etiology_context_scorer.py
backend/tests/evaluation_injection/test_validator_scorer_semantics_v6_0_3.py
```

Also replace every active V6.0.3 local validator/scorer CLI copy, typically one or more of:

```text
kgen_prompt_refinement_v6_0_3/
  local_validator_scorer/cli/validate_and_score_results_v6_0_3.py

KGEN_V6_0_3_WORKFLOW/
  local_validator_scorer/cli/validate_and_score_results_v6_0_3.py

backend/KGEN_V6_0_3_WORKFLOW/
  local_validator_scorer/cli/validate_and_score_results_v6_0_3.py
```

Do not modify the eight answer-key files unless a separate answer-key review is requested.

---

# 3. Required CLI behavior

The corrected CLI must:

1. extract the matching V6.0.3 input ZIP;
2. extract the V6.0.3 Lightning results ZIP;
3. match every result to its scenario;
4. verify the result prompt fingerprint matches the input manifest;
5. adapt the four-field V6.0.3 result to the legacy validator shape;
6. run the current grounding validator;
7. load the real scenario answer key with `allow_legacy_fallback=False`;
8. run the etiology/context scorer;
9. save detailed validation and score information.

It must produce:

```text
local_validation_report.csv
local_validation_report.json
model_scorecard.csv
model_score_details.json
scenario_audit.md
```

The CSV should include at least:

```text
model
scenarioId
validContract
generationAttempts
contractNormalized
accepted
validatorPassed
displayableWithReview
hardErrorCount
qualityErrorCount
unsupportedFactCount
contradictionCount
safetyPass
overallPass
total
grade
diagnosisConsistency
primaryEtiology
contextualEvidence
uncertainty
avoidsDistractors
answerKeySource
```

The CLI must never silently score with an empty answer key.

---

# 4. Required tests

The implementation is incomplete unless all of these pass:

```python
def test_regular_does_not_match_irregular(): ...

def test_sustained_does_not_match_non_sustained(): ...

def test_safe_rounding_is_supported(): ...

def test_empty_uncertainty_gets_partial_credit_without_expected_concepts(): ...

def test_empty_uncertainty_gets_partial_credit_with_expected_concepts(): ...

def test_non_etiologic_technical_conflict_not_required(): ...

def test_etiologic_conflict_is_required(): ...
```

The second empty-uncertainty test is essential. The earlier test covered only an answer key with no expected uncertainty concepts and therefore failed to expose the real bug.

Also verify all eight answer keys load with:

```python
load_scenario_answer_key(
    scenario_id,
    allow_legacy_fallback=False,
)
```

---

# 5. Use the supplied apply script

The supplied script is:

```text
APPLY_LOCAL_VALIDATOR_SCORER_FIXES_V6_0_3_1.ps1
```

It performs the following:

- locates the backend;
- patches the validator and scorer idempotently;
- replaces active validator/scorer CLI copies;
- updates the targeted semantic tests;
- compiles the patched modules;
- verifies all eight real answer keys;
- runs the targeted V6.0.3 tests;
- writes an apply report.

Run it from the project root:

```powershell
powershell -ExecutionPolicy Bypass -File `
  .\KGEN_VALIDATOR_SCORER_FIX_HANDOFF_V6_0_3_1\APPLY_LOCAL_VALIDATOR_SCORER_FIXES_V6_0_3_1.ps1 `
  -ProjectRoot "."
```

Expected completion:

```text
V6.0.3.1 validator/scorer fixes applied successfully.
```

Do not proceed to packaging if the tests fail.

---

# 6. Create the source-only review ZIP

After the first command passes, run:

```powershell
powershell -ExecutionPolicy Bypass -File `
  .\KGEN_VALIDATOR_SCORER_FIX_HANDOFF_V6_0_3_1\CREATE_LOCAL_VALIDATOR_SCORER_REVIEW_ZIP_V6_0_3_1.ps1 `
  -ProjectRoot "."
```

This command does **not** run validation or scoring. It only creates a review package.

The output will be similar to:

```text
validator_scorer_handoff/
  KGEN_LOCAL_VALIDATOR_SCORER_REVIEW_V6_0_3_1_YYYYMMDD-HHMMSS.zip
```

The ZIP will contain:

```text
backend/app/evaluation_injection/response_validator.py
backend/app/evaluation_injection/etiology_context_scorer.py
backend/app/evaluation_injection/answer_key_loader.py
backend/app/evaluation_injection/compatibility_adapter.py
backend/app/evaluation_injection/response_contract.py
backend/data/evaluation_answer_keys/*.json
backend/tests/evaluation_injection/*.py
local_validator_scorer/cli/validate_and_score_results_v6_0_3.py
PACKAGE_MANIFEST.json
CHANGED_FILE_INVENTORY.txt
APPLY_VALIDATOR_SCORER_FIXES_RESULT.json
```

It intentionally excludes:

- secrets and `.env` files;
- model weights;
- Hugging Face cache;
- waveform arrays;
- the full backend data directory;
- Lightning result files;
- the Lightning input package.

---

# 7. What the user does after packaging

Upload only the generated review ZIP to the existing ChatGPT thread.

The V6.0.3 Lightning result ZIP and matching V6.0.3 input ZIP have already been supplied in that thread. ChatGPT can then run independent revalidation and rescoring using the corrected source and answer keys.

The user does **not** need to run:

```text
VALIDATE_AND_SCORE_RESULTS.ps1
```

and does not need to generate the reports locally.

---

# 8. Required deliverables from the working thread

The working thread must return:

```text
1. Updated response_validator.py
2. Updated etiology_context_scorer.py
3. Updated validate_and_score_results_v6_0_3.py
4. Updated validator/scorer semantic test file
5. Test output showing the targeted tests passed
6. Answer-key verification showing all eight keys loaded with no fallback
7. Generated KGEN_LOCAL_VALIDATOR_SCORER_REVIEW_V6_0_3_1_*.zip
8. Changed-file inventory
```

Do not return only an explanation. Apply the changes to the current repository and produce the ZIP.

---

# 9. Acceptance criteria

The fix is accepted only when:

- the CLI imports and uses `load_scenario_answer_key`;
- `allow_legacy_fallback=False` is enforced;
- no empty answer-key fallback exists;
- an empty uncertainty array receives configured partial credit even when acceptable uncertainty concepts are listed;
- `regular` does not match `irregular`;
- `sustained VT` does not match `non-sustained VT`;
- safe numeric rounding passes;
- non-etiologic technical conflicts are not required in presentation text;
- all targeted tests pass;
- all eight answer keys load successfully;
- the review ZIP contains the patched source and eight answer keys;
- no Lightning/model rerun is performed.
