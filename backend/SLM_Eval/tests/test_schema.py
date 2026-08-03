"""Structural schema validation for each episode record (episode-slm-eval-v1)."""
TOP = ["schemaVersion","episodeId","incidentId","capturedAt","detectionSource","mode",
       "patient","episode","ecg","ppg","vitals","labs","clinicalContext"]
PATIENT = ["disclaimer","name","mrn","dob","age","sex","heightCm","weightKg","room",
           "admissionDate","codeStatus","primaryDiagnosis","history","homeMedications","allergies","infusions"]
EPISODE = ["type","display","severity","state","analysisStatus","autoTriggered","triggerHeartRate","durationSeconds"]
ECG = ["leadNames","sampleRate","durationSeconds","gridPaperSpeedMmPerSec","gainMmPerMv","waveform","measurements"]
MEAS = ["rhythm","ventricularRateBpm","atrialRateBpm","regularity","qrsDurationMs","qtcMs","prMs",
        "axisDeg","pWavePresent","stDeviationMm","morphology","ectopyPer10s","preEventNote"]
PPG = ["sampleRate","durationSeconds","unit","perfusionIndexPct","signalQuality","spo2Pct","waveform"]
VITALS = ["spo2Pct","heartRateBpm","respiratoryRateBpm","temperatureC","bloodPressure"]
BP = ["systolic","diastolic","map","note"]

def _has(d, keys, ctx):
    missing = [k for k in keys if k not in d]
    assert not missing, f"{ctx}: missing fields {missing}"

def test_top_level(episodes, episode_id):
    _has(episodes[episode_id], TOP, f"{episode_id} top-level")

def test_schema_version(episodes, episode_id):
    assert episodes[episode_id]["schemaVersion"] == "episode-slm-eval-v1"

def test_patient_block(episodes, episode_id):
    _has(episodes[episode_id]["patient"], PATIENT, f"{episode_id} patient")

def test_episode_block(episodes, episode_id):
    e = episodes[episode_id]["episode"]; _has(e, EPISODE, f"{episode_id} episode")
    assert e["severity"] in ("critical","warning","info")

def test_ecg_block(episodes, episode_id):
    ecg = episodes[episode_id]["ecg"]; _has(ecg, ECG, f"{episode_id} ecg")
    _has(ecg["measurements"], MEAS, f"{episode_id} ecg.measurements")

def test_ppg_block(episodes, episode_id):
    _has(episodes[episode_id]["ppg"], PPG, f"{episode_id} ppg")

def test_vitals_block(episodes, episode_id):
    v = episodes[episode_id]["vitals"]; _has(v, VITALS, f"{episode_id} vitals")
    _has(v["bloodPressure"], BP, f"{episode_id} vitals.bloodPressure")

def test_labs_nonempty(episodes, episode_id):
    labs = episodes[episode_id]["labs"]
    assert isinstance(labs, dict) and len(labs) >= 3, f"{episode_id}: labs must have >=3 entries"
    for name, lab in labs.items():
        assert set(("value","unit","reference","flag")).issubset(lab), f"{episode_id}.labs.{name} malformed"

def test_context_is_list(episodes, episode_id):
    assert isinstance(episodes[episode_id]["clinicalContext"]["recentEvents"], list)
