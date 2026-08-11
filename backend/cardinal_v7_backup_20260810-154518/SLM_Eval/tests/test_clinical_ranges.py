"""Physiologic plausibility of vitals and structural validity of labs. Nulls are allowed
(e.g., a pulseless VF arrest has null SpO2/BP)."""
RANGES = {  # field: (min, max) inclusive plausible bounds
 "spo2Pct": (40, 100), "heartRateBpm": (0, 350), "respiratoryRateBpm": (0, 80), "temperatureC": (25.0, 45.0),
}
BP_RANGES = {"systolic": (0, 300), "diastolic": (0, 200), "map": (0, 250)}

def test_vitals_plausible(episodes, episode_id):
    v = episodes[episode_id]["vitals"]
    for f,(lo,hi) in RANGES.items():
        val = v.get(f)
        if val is None: continue
        assert lo <= val <= hi, f"{episode_id}.vitals.{f}={val} out of [{lo},{hi}]"

def test_bp_plausible(episodes, episode_id):
    bp = episodes[episode_id]["vitals"]["bloodPressure"]
    for f,(lo,hi) in BP_RANGES.items():
        val = bp.get(f)
        if val is None: continue
        assert lo <= val <= hi, f"{episode_id}.bp.{f}={val} out of [{lo},{hi}]"
    if bp.get("systolic") and bp.get("diastolic"):
        assert bp["systolic"] >= bp["diastolic"], f"{episode_id}: systolic < diastolic"

def test_spo2_consistency(episodes, episode_id):
    """PPG spo2 and vitals spo2 should agree when both present."""
    v = episodes[episode_id]["vitals"]["spo2Pct"]; p = episodes[episode_id]["ppg"]["spo2Pct"]
    if v is not None and p is not None:
        assert v == p, f"{episode_id}: vitals SpO2 {v} != ppg SpO2 {p}"

UNITLESS = {"ph", "inr"}  # dimensionless labs legitimately carry no unit

def test_labs_values_numeric(episodes, episode_id):
    for name, lab in episodes[episode_id]["labs"].items():
        v = lab["value"]
        assert isinstance(v,(int,float)), f"{episode_id}.labs.{name}.value not numeric: {v!r}"
        assert lab["unit"] != "" or name.lower() in UNITLESS, f"{episode_id}.labs.{name} missing unit"

def test_temperature_present(episodes, episode_id):
    assert episodes[episode_id]["vitals"]["temperatureC"] is not None
