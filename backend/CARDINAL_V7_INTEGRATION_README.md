# CARDINAL V7 Etiology Engine Integration

This patch is built against the backend supplied in `backend_0.5-1.zip` + `backend_0.5-2.zip` and the frontend supplied in `src(2).zip`.

## What changes

- Replaces the evaluation-injection SLM path with the V7 **Etiology Engine** prompt and nine-field JSON contract.
- Integrates all 14 SLM_Eval scenarios (001-014).
- Does **not** invoke Phase 6 or the Phase 7 orchestrator for `evaluation_injection` episodes.
- Keeps the existing API Range pre-event -> SLM_Eval episode -> API Range post-event capture/composition code unchanged.
- Keeps Oracle SMART as authentication/routing only; Oracle FHIR context is not appended to the Etiology Engine prompt.
- Supports one or multiple scenario candidates per Oracle patient. With multiple candidates, selection is random across SMART authorizations but deterministic within one SMART authorization so refresh/remount/start cannot switch scenarios halfway through a run.
- Keeps the existing `/api/slm-widget/incidents/{incident_id}` frontend contract by writing a V7 compatibility artifact. No frontend source change is required.
- Bundles the seven uploaded V7 Lightning result sets for precomputed/deployed demo mode.

## Important distinction

The existing `episode_analyzer` and `incident_analyzer` calls remain so the rest of the analytics page/capture artifacts keep working. Their output is **not** passed to the V7 Etiology Engine. The evaluation model input is built only from the sanitized SLM_Eval scenario JSON.

## V7 model input sanitization

Before inference, `app/evaluation_injection/etiology_v7.py`:

- removes `ecg.waveform`
- removes `ppg.waveform`
- removes `ecg.measurements.rhythm`
- removes `episode.type` and `episode.display`
- removes answer-key / ground-truth / rubric containers if present
- preserves ECG measurements, vitals, PPG summary, BP, temperature, labs, patient record, medications/history present in the scenario, and clinical context

The original scenario file is not mutated, so the waveform remains available for episode injection and display.

## Install into the backend

1. Back up the current backend.
2. Extract this patch.
3. Copy the patch contents into the **backend root**, preserving paths and overwriting the listed code files.
4. Do **not** replace your existing `.env` with someone else's file. Run the included environment upsert script instead:

```powershell
cd <your-backend-folder>
PowerShell -ExecutionPolicy Bypass -File .\APPLY_CARDINAL_V7_ENV.ps1
```

To choose a different bundled precomputed profile:

```powershell
PowerShell -ExecutionPolicy Bypass -File .\APPLY_CARDINAL_V7_ENV.ps1 -Profile "google-gemma-4-31B-it"
```

For Render, set the variables from `CARDINAL_V7_ENV_PATCH.txt` in the Render environment settings. The key change required for the six new scenarios is the 14-item `EVALUATION_INJECTION_ALLOWED_SCENARIOS` value.

## Deployed / precomputed mode

Recommended configuration if the deployed demo should show an already-evaluated Lightning answer without hosting the model:

```env
ETIOLOGY_V7_PRECOMPUTED_ENABLED=true
ETIOLOGY_V7_PRECOMPUTED_REQUIRED=true
ETIOLOGY_V7_PRECOMPUTED_ROOT=data/etiology_v7_precomputed
ETIOLOGY_V7_PRECOMPUTED_PROFILE=google-medgemma-27b-it
ETIOLOGY_V7_LIVE_MODEL_ENABLED=false
SLM_MAX_OUTPUT_TOKENS=2500
```

Bundled profiles:

- `google-medgemma-27b-it` — 14/14 contract-valid in the supplied archive
- `google-medgemma-27b-text-it` — 14/14 contract-valid
- `google-gemma-4-31B-it` — 14/14 contract-valid
- `google-gemma-4-26B-A4B-it` — 14/14 contract-valid
- `google-gemma-4-12B-it` — 14/14 contract-valid
- `google-gemma-4-E2B-it` — 14/14 contract-valid
- `google-gemma-4-E4B-it` — 13/14 contract-valid; `FLUTTER-IC-011` is intentionally rejected by the runtime because the supplied result did not satisfy the JSON contract

The patch keeps MedGemma 27B-IT as the default to avoid silently changing your current deployed model choice. If you want the strongest model in the supplied answer-key comparison, switch the env profile to `google-gemma-4-31B-it`.

## Local live-model mode

Use the same integration with your local OpenAI-compatible model endpoint:

```env
ETIOLOGY_V7_PRECOMPUTED_ENABLED=false
ETIOLOGY_V7_LIVE_MODEL_ENABLED=true
SLM_EVAL_ALLOW_MODEL=true
SLM_BASE_URL=http://127.0.0.1:11434/v1
SLM_CHAT_PATH=/chat/completions
SLM_MODEL=<served-model-name>
SLM_MAX_OUTPUT_TOKENS=2500
```

The model receives a single user message containing the V7 instruction block followed by the sanitized episode JSON. Temperature is 0 and the existing client requests JSON-object output.

## Oracle scenario routing

The uploaded backend contained eight exact Oracle Patient IDs:

- `12724065`
- `12724066`
- `12724067`
- `12724068`
- `12724069`
- `12724070`
- `12724071`
- `12742400`

The patch does not invent two additional IDs. It gives each known ID two candidate V7 episodes, which makes all 14 scenarios reachable. The map also retains display-name fallbacks for sandbox names such as Sandy SMART and Baby Boy SMART. If you have two additional exact Oracle Patient resource IDs, add them to `app/evaluation_demo/patient_scenario_map.json` using the same `scenarioIds` format.

Example:

```json
"<real-oracle-patient-id>": {
  "enabled": true,
  "scenarioIds": ["BRASH-013", "AMIO-DDI-014"],
  "baselineSeconds": 10,
  "preSeconds": 6,
  "postSeconds": 6,
  "runSlm": true
}
```

## Capture flow after this patch

```text
Oracle SMART auth / route
        |
        v
Choose mapped V7 scenario (stable-random if patient has 2 candidates)
        |
        v
API Range pre-event waveform
        |
        v
Inject selected SLM_Eval episodic waveform/context
        |
        v
API Range post-event waveform
        |
        v
Persist capture + existing analytics
        |
        v
Build sanitized V7 EPISODE DATA (NO raw waveform, NO upstream rhythm label)
        |
        v
Precomputed V7 response OR live model call
        |
        v
Strict nine-field JSON-contract validation
        |
        v
Existing SLM widget endpoint / existing frontend
```

## Phase 6 behavior

For `evaluation_injection` only:

- `app/episodes.py` skips automatic Phase 7 scheduling.
- `app/evaluation_injection/service.py` no longer calls `phase7_orchestrator.run_incident` or `build_score_and_attach_cardinal`.
- `app/slm_widget/assembler.py` returns the V7 model-owned widget payload directly and does not apply the old deterministic overlay.
- prompt artifacts explicitly record `phase6Used: false` and `phase7OrchestratorUsed: false`.

Normal/non-evaluation episodes keep their existing Phase 7 behavior, so the change is scoped and does not globally switch off working functionality.

## Files produced for each evaluation episode

The episode folder now receives:

- `etiology_v7_prompt.json` — sanitized model input + exact prompt
- `etiology_v7_model_response.json` — full nine-field response + generation metadata
- `cardinal_model_response.json` — V7 compatibility object
- `slm_widget_result_v4.json` — compatibility object for existing tooling
- `evaluation_score.json` — contract/runtime status only; no fake answer-key score is produced at runtime

For frontend compatibility, a V7 response is also stored under the existing incident `phase7/<incident>/slm_response.json` location. That directory name is only a legacy storage path; the Phase 7 orchestrator is not executed for the evaluation episode.

## Verification

Run:

```powershell
python .\VERIFY_CARDINAL_V7_INTEGRATION.py
pytest -q .\tests\evaluation_injection\test_etiology_v7_integration.py
```

The supplied patch was syntax-compiled successfully and the new V7 integration test passed 4/4 in the build environment. A functional smoke test also loaded all 14 waveforms at 220 Hz, sanitized all 14 model inputs, confirmed stable Oracle candidate selection, loaded the precomputed MedGemma V7 response, and returned it through the existing SLM-widget assembler without deterministic overlay.
