"""Critical for a fair evaluation: the SLM-INPUT episode files must NOT contain any ground-truth /
answer fields. Also verifies PHI is flagged synthetic."""
FORBIDDEN_KEYS = {"groundtruth","gt","primaryetiology","mechanism","mustrecommend","mustidentify",
                  "distractors","contributing","answer","answerkey","rubric","expectedetiology"}

def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)

def test_no_ground_truth_keys(episodes, episode_id):
    leaked = {k for k in _walk_keys(episodes[episode_id]) if k.lower() in FORBIDDEN_KEYS}
    assert not leaked, f"{episode_id}: leaked answer-key field(s) into SLM input: {leaked}"

def test_phi_marked_synthetic(episodes, episode_id):
    disc = episodes[episode_id]["patient"].get("disclaimer","").upper()
    assert "SYNTHETIC" in disc or "FICTIONAL" in disc, f"{episode_id}: patient PHI not flagged synthetic"

def test_no_etiology_conclusion_in_input(episodes, episode_id):
    """The input may describe observations but must not hand the model a stated etiology/diagnosis conclusion."""
    ep = episodes[episode_id]["episode"]
    assert "diagnosis" not in ep, f"{episode_id}: episode block should not carry a diagnosis conclusion"
