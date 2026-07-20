#!/usr/bin/env bash
# KardioGenics Phase 6 backend test runner
#
# Run:
#   bash test_phase6_backend.sh
#
# Optional:
#   BASE_URL="http://127.0.0.1:8000" bash test_phase6_backend.sh
#
# Place this file either in the project root (beside backend/) or in backend/.
# The FastAPI backend must already be running.

set -u
set -o pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "$SCRIPT_DIR/backend/main.py" && -d "$SCRIPT_DIR/backend/app" ]]; then
  BACKEND_DIR="$SCRIPT_DIR/backend"
elif [[ -f "$SCRIPT_DIR/main.py" && -d "$SCRIPT_DIR/app" ]]; then
  BACKEND_DIR="$SCRIPT_DIR"
elif [[ -f "$PWD/backend/main.py" && -d "$PWD/backend/app" ]]; then
  BACKEND_DIR="$PWD/backend"
elif [[ -f "$PWD/main.py" && -d "$PWD/app" ]]; then
  BACKEND_DIR="$PWD"
else
  echo "ERROR: Could not find backend/main.py or main.py."
  echo "Place this script in the project root or backend directory."
  exit 1
fi

cd "$BACKEND_DIR" || exit 1

ARTIFACT_DIR="$BACKEND_DIR/phase6_test_artifacts_$TIMESTAMP"
REPORT_FILE="$BACKEND_DIR/phase6_test_report_$TIMESTAMP.txt"
LATEST_REPORT="$BACKEND_DIR/phase6_test_report_latest.txt"
mkdir -p "$ARTIFACT_DIR"
touch "$REPORT_FILE"

if [[ -x "$BACKEND_DIR/.venv/Scripts/python.exe" ]]; then
  PYTHON_CMD="$BACKEND_DIR/.venv/Scripts/python.exe"
elif [[ -x "$BACKEND_DIR/.venv/bin/python" ]]; then
  PYTHON_CMD="$BACKEND_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD="$(command -v python)"
else
  echo "ERROR: Python was not found."
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl was not found."
  exit 1
fi

log() {
  printf '%s\n' "$*" | tee -a "$REPORT_FILE"
}

section() {
  log ""
  log "================================================================================"
  log "$1"
  log "================================================================================"
}

run_and_log() {
  local title="$1"
  shift
  section "$title"
  {
    echo "\$ $*"
    "$@"
    local code=$?
    echo
    echo "EXIT_CODE=$code"
    return "$code"
  } 2>&1 | tee -a "$REPORT_FILE"
}

http_request() {
  local method="$1"
  local url="$2"
  local output="$3"
  local http_code

  http_code="$(
    curl --silent --show-error --location \
      --request "$method" \
      --header "Accept: application/json" \
      --output "$output" \
      --write-out "%{http_code}" \
      "$url" 2>"${output}.curl_error"
  )"
  local curl_code=$?

  printf '%s' "$http_code" > "${output}.http_code"

  log "METHOD=$method"
  log "URL=$url"
  log "HTTP_STATUS=${http_code:-curl_failed}"
  log "CURL_EXIT_CODE=$curl_code"

  if [[ -s "${output}.curl_error" ]]; then
    log "CURL_ERROR:"
    cat "${output}.curl_error" | tee -a "$REPORT_FILE"
  fi
}

pretty_json() {
  local file="$1"
  local title="$2"
  section "$title"

  if [[ ! -s "$file" ]]; then
    log "No response body was saved."
    return
  fi

  "$PYTHON_CMD" - "$file" <<'PY' 2>&1 | tee -a "$REPORT_FILE"
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8", errors="replace")
try:
    data = json.loads(text)
except Exception:
    print(text)
else:
    print(json.dumps(data, indent=2, ensure_ascii=False))
PY
}

url_encode() {
  "$PYTHON_CMD" - "$1" <<'PY'
import sys
from urllib.parse import quote
print(quote(sys.argv[1], safe=""))
PY
}

first_episode_id() {
  "$PYTHON_CMD" - "$1" <<'PY'
import json
import sys

try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print("")
    raise SystemExit

items = data.get("episodes", []) if isinstance(data, dict) else data
if not isinstance(items, list) or not items:
    print("")
elif isinstance(items[0], str):
    print(items[0])
elif isinstance(items[0], dict):
    print(items[0].get("id") or items[0].get("episodeId") or "")
else:
    print("")
PY
}

first_incident_id() {
  "$PYTHON_CMD" - "$1" <<'PY'
import json
import sys

try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print("")
    raise SystemExit

items = data.get("incidents", []) if isinstance(data, dict) else data
if not isinstance(items, list) or not items:
    print("")
elif isinstance(items[0], str):
    print(items[0])
elif isinstance(items[0], dict):
    print(items[0].get("id") or items[0].get("incidentId") or "")
else:
    print("")
PY
}

incident_id_from_files() {
  "$PYTHON_CMD" - "$@" <<'PY'
import json
import sys

def find(value):
    if isinstance(value, dict):
        for key in ("incidentId", "incident_id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        for nested in value.values():
            found = find(nested)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = find(nested)
            if found:
                return found
    return ""

for filename in sys.argv[1:]:
    try:
        data = json.load(open(filename, encoding="utf-8"))
    except Exception:
        continue
    result = find(data)
    if result:
        print(result)
        raise SystemExit

print("")
PY
}

sha256_file() {
  "$PYTHON_CMD" - "$1" <<'PY'
import hashlib
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
if not path.is_file():
    print("")
    raise SystemExit

digest = hashlib.sha256()
with path.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
print(digest.hexdigest())
PY
}

section "RUN INFORMATION"
log "TIMESTAMP=$TIMESTAMP"
log "BACKEND_DIR=$BACKEND_DIR"
log "BASE_URL=$BASE_URL"
log "PYTHON_CMD=$PYTHON_CMD"
log "REPORT_FILE=$REPORT_FILE"
log "ARTIFACT_DIR=$ARTIFACT_DIR"

run_and_log "PYTHON VERSION" "$PYTHON_CMD" --version

run_and_log \
  "DEPENDENCY IMPORT CHECK" \
  "$PYTHON_CMD" -c \
  "import numpy, scipy, fastapi, pytest; print('numpy=', numpy.__version__); print('scipy=', scipy.__version__); print('fastapi=', fastapi.__version__); print('pytest=', pytest.__version__)"

run_and_log \
  "PHASE 6 IMPORT CHECK" \
  "$PYTHON_CMD" -c \
  "from app.analysis.episode_analyzer import episode_analyzer; from app.analysis.incident_analyzer import incident_analyzer; from app.analysis.slm_context import build_phase6_slm_context; from app.episode_routes import router, incident_router; print('Phase 6 imports successful')"

run_and_log "COMPILE CHECK" "$PYTHON_CMD" -m compileall -q app

if [[ -f "$BACKEND_DIR/tests/test_phase6_analysis.py" ]]; then
  run_and_log \
    "PHASE 6 PYTEST" \
    "$PYTHON_CMD" -m pytest tests/test_phase6_analysis.py -v
else
  section "PHASE 6 PYTEST"
  log "SKIPPED: tests/test_phase6_analysis.py was not found."
fi

section "OPENAPI CHECK"
http_request "GET" "$BASE_URL/openapi.json" "$ARTIFACT_DIR/openapi.json"
pretty_json "$ARTIFACT_DIR/openapi.json" "OPENAPI RESPONSE"

section "LIST EPISODES"
http_request "GET" "$BASE_URL/api/episodes" "$ARTIFACT_DIR/episodes.json"
pretty_json "$ARTIFACT_DIR/episodes.json" "EPISODES RESPONSE"

EPISODE_ID="$(first_episode_id "$ARTIFACT_DIR/episodes.json")"

if [[ -z "$EPISODE_ID" ]]; then
  section "STOPPED: NO EPISODE"
  log "No episode was found."
  log "Open the existing Seven Lead page, select INCART, let it replay until an episode is captured, then rerun this script."
  cp "$REPORT_FILE" "$LATEST_REPORT"
  log "Upload this file in your next prompt:"
  log "$LATEST_REPORT"
  exit 2
fi

ENCODED_EPISODE_ID="$(url_encode "$EPISODE_ID")"
section "SELECTED EPISODE"
log "EPISODE_ID=$EPISODE_ID"
log "ENCODED_EPISODE_ID=$ENCODED_EPISODE_ID"

section "EPISODE DETAIL"
http_request "GET" "$BASE_URL/api/episodes/$ENCODED_EPISODE_ID" "$ARTIFACT_DIR/episode_detail.json"
pretty_json "$ARTIFACT_DIR/episode_detail.json" "EPISODE DETAIL RESPONSE"

EPISODE_STORAGE_PATH="$(
  "$PYTHON_CMD" - <<'PY' 2>/dev/null
try:
    from app.config import settings
    print(settings.EPISODE_STORAGE_PATH)
except Exception:
    print("data/episodes")
PY
)"
EPISODE_STORAGE_PATH="${EPISODE_STORAGE_PATH:-data/episodes}"

if [[ "$EPISODE_STORAGE_PATH" = /* ]] || [[ "$EPISODE_STORAGE_PATH" =~ ^[A-Za-z]:[\\/].* ]]; then
  WAVEFORM_PATH="$EPISODE_STORAGE_PATH/$EPISODE_ID/waveforms.npz"
else
  WAVEFORM_PATH="$BACKEND_DIR/$EPISODE_STORAGE_PATH/$EPISODE_ID/waveforms.npz"
fi

HASH_BEFORE="$(sha256_file "$WAVEFORM_PATH")"
section "WAVEFORM HASH BEFORE ANALYSIS"
log "WAVEFORM_PATH=$WAVEFORM_PATH"
log "SHA256_BEFORE=${HASH_BEFORE:-not_found}"

section "EPISODE ANALYSIS BEFORE"
http_request "GET" "$BASE_URL/api/episodes/$ENCODED_EPISODE_ID/analysis" "$ARTIFACT_DIR/episode_analysis_before.json"
pretty_json "$ARTIFACT_DIR/episode_analysis_before.json" "EPISODE ANALYSIS BEFORE RESPONSE"

section "RUN EPISODE ANALYSIS"
http_request "POST" "$BASE_URL/api/episodes/$ENCODED_EPISODE_ID/analyze?force=false" "$ARTIFACT_DIR/episode_analyze_post.json"
pretty_json "$ARTIFACT_DIR/episode_analyze_post.json" "EPISODE ANALYZE RESPONSE"

section "EPISODE ANALYSIS AFTER"
http_request "GET" "$BASE_URL/api/episodes/$ENCODED_EPISODE_ID/analysis" "$ARTIFACT_DIR/episode_analysis_after.json"
pretty_json "$ARTIFACT_DIR/episode_analysis_after.json" "EPISODE ANALYSIS AFTER RESPONSE"

HASH_AFTER="$(sha256_file "$WAVEFORM_PATH")"
section "WAVEFORM HASH AFTER ANALYSIS"
log "SHA256_AFTER=${HASH_AFTER:-not_found}"
if [[ -n "$HASH_BEFORE" && -n "$HASH_AFTER" ]]; then
  if [[ "$HASH_BEFORE" == "$HASH_AFTER" ]]; then
    log "RAW_WAVEFORM_IMMUTABILITY=PASS"
  else
    log "RAW_WAVEFORM_IMMUTABILITY=FAIL"
  fi
else
  log "RAW_WAVEFORM_IMMUTABILITY=NOT_CHECKED"
fi

section "REBUILD INCIDENTS"
http_request "POST" "$BASE_URL/api/incidents/rebuild" "$ARTIFACT_DIR/incidents_rebuild.json"
pretty_json "$ARTIFACT_DIR/incidents_rebuild.json" "INCIDENT REBUILD RESPONSE"

section "LIST INCIDENTS"
http_request "GET" "$BASE_URL/api/incidents" "$ARTIFACT_DIR/incidents.json"
pretty_json "$ARTIFACT_DIR/incidents.json" "INCIDENTS RESPONSE"

INCIDENT_ID="$(incident_id_from_files \
  "$ARTIFACT_DIR/episode_detail.json" \
  "$ARTIFACT_DIR/episode_analyze_post.json" \
  "$ARTIFACT_DIR/episode_analysis_after.json")"

if [[ -z "$INCIDENT_ID" ]]; then
  INCIDENT_ID="$(first_incident_id "$ARTIFACT_DIR/incidents.json")"
fi

if [[ -z "$INCIDENT_ID" ]]; then
  section "STOPPED: NO INCIDENT"
  log "Episode analysis was tested, but no incident ID was found."
  cp "$REPORT_FILE" "$LATEST_REPORT"
  log "Upload this file in your next prompt:"
  log "$LATEST_REPORT"
  exit 3
fi

ENCODED_INCIDENT_ID="$(url_encode "$INCIDENT_ID")"
section "SELECTED INCIDENT"
log "INCIDENT_ID=$INCIDENT_ID"
log "ENCODED_INCIDENT_ID=$ENCODED_INCIDENT_ID"

section "INCIDENT ANALYSIS BEFORE"
http_request "GET" "$BASE_URL/api/incidents/$ENCODED_INCIDENT_ID/analysis" "$ARTIFACT_DIR/incident_analysis_before.json"
pretty_json "$ARTIFACT_DIR/incident_analysis_before.json" "INCIDENT ANALYSIS BEFORE RESPONSE"

section "RUN INCIDENT ANALYSIS"
http_request "POST" "$BASE_URL/api/incidents/$ENCODED_INCIDENT_ID/analyze?force=false" "$ARTIFACT_DIR/incident_analyze_post.json"
pretty_json "$ARTIFACT_DIR/incident_analyze_post.json" "INCIDENT ANALYZE RESPONSE"

section "INCIDENT ANALYSIS AFTER"
http_request "GET" "$BASE_URL/api/incidents/$ENCODED_INCIDENT_ID/analysis" "$ARTIFACT_DIR/incident_analysis_after.json"
pretty_json "$ARTIFACT_DIR/incident_analysis_after.json" "INCIDENT ANALYSIS AFTER RESPONSE"

section "SLM CONTEXT"
http_request "GET" "$BASE_URL/api/incidents/$ENCODED_INCIDENT_ID/slm-context" "$ARTIFACT_DIR/slm_context.json"
pretty_json "$ARTIFACT_DIR/slm_context.json" "SLM CONTEXT RESPONSE"

section "AUTOMATED VALIDATION"
"$PYTHON_CMD" - "$ARTIFACT_DIR" "$EPISODE_ID" "$INCIDENT_ID" <<'PY' 2>&1 | tee -a "$REPORT_FILE"
import json
import pathlib
import sys
from typing import Any

artifact_dir = pathlib.Path(sys.argv[1])
episode_id = sys.argv[2]
incident_id = sys.argv[3]

def load(name):
    path = artifact_dir / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"__load_error__": str(exc)}

def get_path(value, *paths):
    for path in paths:
        current = value
        ok = True
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                ok = False
                break
            current = current[part]
        if ok:
            return current
    return None

def find_key(value: Any, target: str):
    if isinstance(value, dict):
        if target in value:
            return value[target]
        for nested in value.values():
            found = find_key(nested, target)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = find_key(nested, target)
            if found is not None:
                return found
    return None

def find_banned(value: Any, path="$"):
    banned = {
        "rawWaveforms",
        "filteredWaveforms",
        "raw_mv",
        "centered_mv",
        "segmentedBeats",
        "beatArrays",
    }
    results = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in banned:
                results.append(f"{path}.{key}")
            results.extend(find_banned(nested, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, nested in enumerate(value[:200]):
            results.extend(find_banned(nested, f"{path}[{index}]"))
    return results

def find_large_numeric_arrays(value: Any, path="$"):
    results = []
    if isinstance(value, dict):
        for key, nested in value.items():
            results.extend(find_large_numeric_arrays(nested, f"{path}.{key}"))
    elif isinstance(value, list):
        if (
            len(value) > 100
            and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
        ):
            results.append((path, len(value)))
        else:
            for index, nested in enumerate(value[:200]):
                results.extend(find_large_numeric_arrays(nested, f"{path}[{index}]"))
    return results

episode = load("episode_analysis_after.json")
incident = load("incident_analysis_after.json")
slm = load("slm_context.json")

checks = []

episode_status = get_path(episode, "status", "analysis.status", "result.status")
checks.append(("Episode status is ready or partial", episode_status in {"ready", "partial"}, episode_status))

returned_episode_id = get_path(episode, "episodeId", "episode_id", "analysis.episodeId")
checks.append(("Episode ID matches", returned_episode_id in {None, episode_id}, returned_episode_id))

independent = find_key(episode, "isIndependentDiagnosis")
checks.append(("Episode isIndependentDiagnosis is false", independent is False, independent))

quality = get_path(episode, "signalQuality", "analysis.signalQuality")
checks.append(("Signal quality exists", isinstance(quality, dict) and bool(quality), type(quality).__name__))

rpeaks = get_path(episode, "rPeakAnalysis", "analysis.rPeakAnalysis")
checks.append(("R-peak analysis exists", isinstance(rpeaks, dict) and bool(rpeaks), type(rpeaks).__name__))

morphology = get_path(episode, "morphology", "analysis.morphology")
checks.append(("Morphology exists", isinstance(morphology, dict) and bool(morphology), type(morphology).__name__))

incident_status = get_path(incident, "status", "analysis.status", "result.status")
checks.append(("Incident status is ready or partial", incident_status in {"ready", "partial"}, incident_status))

returned_incident_id = get_path(incident, "incidentId", "incident_id", "analysis.incidentId")
checks.append(("Incident ID matches", returned_incident_id in {None, incident_id}, returned_incident_id))

slm_independent = find_key(slm, "isIndependentDiagnosis")
checks.append(("SLM context preserves isIndependentDiagnosis=false", slm_independent is False, slm_independent))

deterministic = get_path(slm, "deterministicEcgEvidence", "analysis.deterministicEcgEvidence")
checks.append(("SLM context has deterministic ECG evidence", isinstance(deterministic, dict) and bool(deterministic), type(deterministic).__name__))

banned = find_banned(slm)
checks.append(("SLM context excludes raw waveform keys", not banned, banned))

large_arrays = find_large_numeric_arrays(slm)
checks.append(("SLM context has no numeric arrays longer than 100", not large_arrays, large_arrays))

passed = 0
failed = 0
for name, ok, detail in checks:
    print(f"{'PASS' if ok else 'FAIL'}: {name}")
    print(f"  detail={detail!r}")
    if ok:
        passed += 1
    else:
        failed += 1

print()
print(f"VALIDATION_PASSED={passed}")
print(f"VALIDATION_FAILED={failed}")
PY

section "HTTP STATUS SUMMARY"
for code_file in "$ARTIFACT_DIR"/*.http_code; do
  [[ -e "$code_file" ]] || continue
  name="$(basename "$code_file" .http_code)"
  code="$(cat "$code_file")"
  log "$name=$code"
done

cp "$REPORT_FILE" "$LATEST_REPORT"

section "FINAL RESULT"
log "Timestamped report:"
log "$REPORT_FILE"
log ""
log "Upload this stable report file in your next prompt:"
log "$LATEST_REPORT"
log ""
log "Raw JSON responses:"
log "$ARTIFACT_DIR"
log ""
log "Note: slm_context.json may contain clinical context. Review it before sharing outside your approved development environment."
