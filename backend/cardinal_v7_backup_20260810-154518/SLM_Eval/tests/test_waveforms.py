"""Waveform integrity: correct leads, sample counts, numeric types, plausible ranges."""
SIX_LEADS = ["I","II","III","aVR","aVL","aVF"]

def test_six_leads(episodes, episode_id):
    assert episodes[episode_id]["ecg"]["leadNames"] == SIX_LEADS

def test_ecg_sample_count(episodes, episode_id):
    ecg = episodes[episode_id]["ecg"]
    expected = round(ecg["sampleRate"] * ecg["durationSeconds"])
    for lead in SIX_LEADS:
        arr = ecg["waveform"][lead]
        assert len(arr) == expected, f"{episode_id} lead {lead}: {len(arr)} != {expected}"

def test_ecg_numeric_and_range(episodes, episode_id):
    ecg = episodes[episode_id]["ecg"]
    for lead in SIX_LEADS:
        arr = ecg["waveform"][lead]
        assert all(isinstance(v,(int,float)) for v in arr), f"{episode_id} {lead}: non-numeric sample"
        assert max(abs(v) for v in arr) < 6.0, f"{episode_id} {lead}: implausible ECG amplitude (>6 mV)"

def test_ecg_sample_rate(episodes, episode_id):
    assert episodes[episode_id]["ecg"]["sampleRate"] > 0

def test_ppg_sample_count(episodes, episode_id):
    ppg = episodes[episode_id]["ppg"]
    expected = round(ppg["sampleRate"] * ppg["durationSeconds"])
    assert len(ppg["waveform"]) == expected, f"{episode_id} ppg: {len(ppg['waveform'])} != {expected}"
    assert all(isinstance(v,(int,float)) for v in ppg["waveform"])

def test_ppg_quality_enum(episodes, episode_id):
    assert episodes[episode_id]["ppg"]["signalQuality"] in ("good","fair","poor","no_pulse")
