"""Answer key completeness + rubric integrity, and index/manifest consistency."""
REQ = ["display","primaryEtiology","mechanism","contributing","mustIdentify","mustRecommend","distractors"]

def test_scoring_sums_to_100(answer_key):
    s = answer_key["scoring"]
    parts = ["rhythm_identification","primary_etiology","contributing_factors","recommended_actions","avoids_distractors"]
    assert sum(s[p] for p in parts) == 100, f"rubric weights sum to {sum(s[p] for p in parts)}, expected 100"
    assert s["total"] == 100

def test_every_episode_has_answer(episodes, answer_key):
    ak = answer_key["episodes"]
    for eid in episodes:
        assert eid in ak, f"answer key missing entry for {eid}"

def test_answer_entries_complete(answer_key):
    for eid, e in answer_key["episodes"].items():
        for k in REQ:
            assert k in e, f"{eid}: answer missing {k}"
        assert isinstance(e["primaryEtiology"], str) and len(e["primaryEtiology"]) > 10, f"{eid}: weak primaryEtiology"
        for lk in ("contributing","mustIdentify","mustRecommend","distractors"):
            assert isinstance(e[lk], list) and len(e[lk]) >= 1, f"{eid}.{lk} must be a non-empty list"

def test_index_matches_files(index, episodes):
    assert index["count"] == len(episodes), "index count != number of episode files"
    for m in index["episodes"]:
        assert m["episodeId"] in episodes, f"index references unknown episode {m['episodeId']}"
