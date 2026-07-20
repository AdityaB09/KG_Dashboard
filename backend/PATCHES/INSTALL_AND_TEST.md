# KardioGenics Phase 7 Installation

## What this changes

Phase 7 is additive. It does not replace the working Phase 6
analysis modules, incident grouping, Oracle context service,
waveform storage, frontend pages, or existing manual analysis
routes.

New package:

```text
backend/app/phase7/
```

Existing files receiving small edits:

```text
backend/app/episodes.py
backend/main.py
backend/.env
```

## 1. Copy the package

Copy:

```text
backend/app/phase7
```

into the project's existing:

```text
backend/app/
```

Copy:

```text
backend/tests/test_phase7.py
```

into:

```text
backend/tests/
```

## 2. Modify episodes.py

Open:

```text
backend/app/episodes.py
```

Find the `self.publish(...)` call whose event type is:

```text
episode.captured
```

Place the contents of:

```text
PATCHES/episodes.py_insertion.py
```

immediately after that complete `self.publish(...)` call.

This location is important. At that point:

- waveform storage succeeded,
- metadata storage succeeded,
- the incident was registered,
- clinical_context.json exists,
- analysis.json exists,
- the capture notification was published.

The hook only schedules a background task. It does not block the
live waveform stream.

## 3. Modify main.py

Add the import and include call from:

```text
PATCHES/main.py_insertion.py
```

Do not remove or replace any existing routers.

## 4. Add environment variables

Append the contents of:

```text
PATCHES/env_additions.txt
```

to:

```text
backend/.env
```

## 5. Compile

From the backend folder:

```powershell
python -m py_compile app\phase7\*.py
python -m compileall app
```

## 6. Test

```powershell
python -m pytest tests\test_phase6_analysis.py tests\test_phase6_timing_validation.py tests\test_phase7.py -v
```

## 7. Start

```powershell
python -m uvicorn main:app --reload
```

## 8. Confirm configuration

Browser:

```text
http://127.0.0.1:8000/api/phase7/health
```

Expected:

```json
{
  "ok": true,
  "enabled": true,
  "autoRunAfterCapture": true,
  "loadClinicalContext": true
}
```

## 9. Normal automatic test

1. Keep the backend running.
2. Open the existing Seven Lead page.
3. Start or continue INCART replay.
4. Wait for a new `V`-triggered episode capture.
5. Do not use Swagger and do not manually POST episode analysis.
6. Open the existing incident list:
   `http://127.0.0.1:8000/api/incidents`
7. Copy the latest incident id.
8. Open the Phase 7 status:
   `http://127.0.0.1:8000/api/phase7/incidents/{INCIDENT_ID}/status`

Expected final state:

```text
ready_for_slm
```

or, when automatic SLM inference is enabled:

```text
complete
```

## 10. Inspect generated evidence and prompt

```text
http://127.0.0.1:8000/api/phase7/incidents/{INCIDENT_ID}/evidence
http://127.0.0.1:8000/api/phase7/incidents/{INCIDENT_ID}/prompt
```

Existing endpoints remain available:

```text
http://127.0.0.1:8000/api/incidents/{INCIDENT_ID}/analysis
http://127.0.0.1:8000/api/incidents/{INCIDENT_ID}/context
http://127.0.0.1:8000/api/incidents/{INCIDENT_ID}/slm-context
```

## Manual/debug route

Normal operation does not need this route.

For forced debugging only:

```text
POST /api/phase7/incidents/{incident_id}/run
```

Available query parameters:

```text
force=false
force_context=false
run_slm=false
patient_id=
```

## Automatic behavior

After installation, the normal sequence is:

```text
episode persisted
→ incident registered
→ episode analysis
→ all incident episode views checked/analyzed
→ incident analysis
→ Oracle/FHIR context reused or loaded
→ compact Phase 6 SLM context
→ Phase 7 evidence classification
→ prompt safety validation
→ prompt persisted
→ optional SLM inference
```

No manual Phase 6 POST is required during normal operation.

## Oracle security requirement

The ECG-only path is fully automatic.

For `ECG_PLUS_FHIR`, one of these must exist:

- a valid Oracle SMART session in backend memory, or
- a configured and accessible test-patient setup.

A protected Oracle SMART login cannot be bypassed. After a backend
restart, the current in-memory token store is empty, so the user must
complete Oracle SMART login once again. After login, subsequent
incident context loading and prompt building are automatic until the
session expires or the backend restarts.

## Stored Phase 7 files

```text
<INCIDENT_STORAGE_PATH>/phase7/<incident-id>/
├── status.json
├── evidence_package.json
├── prompt_package.json
└── slm_response.json        # only when model inference runs
```
