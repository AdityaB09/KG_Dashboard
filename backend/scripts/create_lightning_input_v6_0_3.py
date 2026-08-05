from __future__ import annotations
import argparse, hashlib, json, shutil, tempfile, zipfile
from pathlib import Path
from typing import Any

SCENARIOS = [
 "VFIB-STEMI-001","TORSADES-LQT-002","VT-ISCHEMIC-003","AFIB-RVR-SEPSIS-004",
 "CHB-HYPERK-005","BRADY-DIGTOX-006","SVT-PSVT-007","NSVT-ECTOPY-008",
]
ARTIFACT_NAMES = {
 "evidence": ["evidence_package.json","grounded_model_input.json","scoped_evidence.json"],
 "diagnostic": ["diagnostic_event.json"],
 "validator": ["validator_evidence.json","evidence_package.json","grounded_model_input.json"],
}

def load(path: Path) -> Any: return json.loads(path.read_text(encoding='utf-8'))
def dump(path: Path, value: Any): path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def hash_value(value: Any) -> str: return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def find_scenario_dir(episodes: Path, scenario: str) -> Path:
    candidates=[]
    for d in episodes.iterdir() if episodes.is_dir() else []:
        if not d.is_dir(): continue
        score=2 if scenario in d.name else 0
        if score==0:
            for name in ("metadata.json","status.json","diagnostic_event.json","evidence_package.json"):
                p=d/name
                if p.is_file() and scenario in p.read_text(encoding='utf-8',errors='ignore'):
                    score=1; break
        if score: candidates.append((d.stat().st_mtime,score,d))
    if not candidates: raise FileNotFoundError(f"No saved episode found for {scenario}")
    candidates.sort(reverse=True); return candidates[0][2]

def find_artifact(folder: Path, names: list[str]) -> Path:
    for name in names:
        direct=folder/name
        if direct.is_file(): return direct
    for name in names:
        matches=sorted(folder.rglob(name), key=lambda p:p.stat().st_mtime, reverse=True)
        if matches: return matches[0]
    raise FileNotFoundError(f"Missing one of {names} under {folder}")

def normalize_envelope(raw: dict[str,Any], scenario: str) -> dict[str,Any]:
    # Common artifacts sometimes wrap the actual V4 envelope.
    for key in ("evidenceBundle","scopedEvidence","suppliedEvidence","groundedModelInput"):
        candidate=raw.get(key)
        if isinstance(candidate,dict) and candidate.get('schemaVersion')=='slm-evidence-envelope-v4': raw=candidate; break
    if raw.get('schemaVersion')!='slm-evidence-envelope-v4':
        raise ValueError(f"{scenario}: evidence is not slm-evidence-envelope-v4")
    if raw.get('clinicalPromptMode')!='episode_pack_only':
        raise ValueError(f"{scenario}: clinicalPromptMode must be episode_pack_only")
    return raw

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--backend-root',required=True); ap.add_argument('--output-zip',required=True); ap.add_argument('--output-directory',default='')
    a=ap.parse_args(); backend=Path(a.backend_root).resolve(); episodes=backend/'data/episodes'; outzip=Path(a.output_zip).resolve()
    sys_path=str(backend)
    import sys; sys.path.insert(0,sys_path)
    from app.evaluation_injection.grounded_prompt_builder import build_grounded_messages
    from app.evaluation_injection.model_clinical_evidence import build_model_clinical_evidence
    work=Path(a.output_directory).resolve() if a.output_directory else Path(tempfile.mkdtemp(prefix='kgen-v603-input-'))
    if work.exists(): shutil.rmtree(work)
    work.mkdir(parents=True)
    items=[]
    for scenario in SCENARIOS:
        folder=find_scenario_dir(episodes,scenario)
        evidence=normalize_envelope(load(find_artifact(folder,ARTIFACT_NAMES['evidence'])),scenario)
        diagnostic=load(find_artifact(folder,ARTIFACT_NAMES['diagnostic']))
        validator=load(find_artifact(folder,ARTIFACT_NAMES['validator']))
        clinical=build_model_clinical_evidence(evidence_bundle=evidence)
        messages=build_grounded_messages(evidence_bundle=evidence)
        fingerprint=hash_value({"promptVersion":"episode-pack-phase6-v6.0.3","messages":messages,"clinicalEvidence":clinical})
        itemdir=work/'items'/scenario
        dump(itemdir/'messages.json',messages); dump(itemdir/'model_clinical_evidence.json',clinical)
        dump(itemdir/'diagnostic_event.json',diagnostic); dump(itemdir/'validator_evidence.json',validator)
        metadata={"scenarioId":scenario,"sourceEpisodeFolder":folder.name,"promptFingerprint":fingerprint,"promptVersion":"episode-pack-phase6-v6.0.3","responseContractVersion":"model-clinical-output-v6.0.3","contextMode":"episode_pack_only"}
        dump(itemdir/'metadata.json',metadata)
        items.append({"scenarioId":scenario,"promptFingerprint":fingerprint,"messages":messages,"paths":{k:f"items/{scenario}/{v}" for k,v in {"messages":"messages.json","modelClinicalEvidence":"model_clinical_evidence.json","diagnosticEvent":"diagnostic_event.json","validatorEvidence":"validator_evidence.json","metadata":"metadata.json"}.items()}})
    manifest={"schemaVersion":"kgen-colab-medical-batch-v3","promptVersion":"episode-pack-phase6-v6.0.3","responseContractVersion":"model-clinical-output-v6.0.3","contextMode":"episode_pack_only","itemCount":8,"items":items}
    dump(work/'manifest.json',manifest)
    outzip.parent.mkdir(parents=True,exist_ok=True)
    if outzip.exists(): outzip.unlink()
    with zipfile.ZipFile(outzip,'w',zipfile.ZIP_DEFLATED) as z:
        for f in work.rglob('*'):
            if f.is_file(): z.write(f,f.relative_to(work).as_posix())
    print(json.dumps({"outputZip":str(outzip),"itemCount":8,"episodeRerecordingRequired":False,"status":"READY FOR LIGHTNING AI"},indent=2))

if __name__=='__main__': main()
