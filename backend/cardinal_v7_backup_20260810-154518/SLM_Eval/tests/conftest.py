"""Shared fixtures for the CARDINAL SLM-eval dataset tests."""
import json, os, glob, pytest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EP_DIR = os.path.join(ROOT, "episodes")

def _load(p):
    with open(p) as f: return json.load(f)

def episode_files():
    return sorted(glob.glob(os.path.join(EP_DIR, "*.json")))

@pytest.fixture(scope="session")
def episodes():
    return {os.path.basename(p)[:-5]: _load(p) for p in episode_files()}

@pytest.fixture(scope="session")
def answer_key():
    return _load(os.path.join(ROOT, "answer_key.json"))

@pytest.fixture(scope="session")
def index():
    return _load(os.path.join(ROOT, "index.json"))

# parametrize helper: one test invocation per episode id
def pytest_generate_tests(metafunc):
    if "episode_id" in metafunc.fixturenames:
        ids = [os.path.basename(p)[:-5] for p in episode_files()]
        metafunc.parametrize("episode_id", ids)
