from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.evaluation_injection.cardinal_bridge import rerun_grounded_from_saved_input
from app.evaluation_injection.grounded_cardinal_client import model_slug
from scripts.model_selection_report import build_report, write_csv, write_html


def now(): return datetime.now(timezone.utc).isoformat()

def atomic(path: Path, value: dict[str, Any]):
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(value,indent=2,ensure_ascii=False),encoding="utf-8"); tmp.replace(path)


def episode_root(): return Path(settings.EPISODE_STORAGE_PATH).resolve()


def latest_by_scenario():
    result={}
    for path in episode_root().glob("*/grounded_model_input.json"):
        try: payload=json.loads(path.read_text(encoding="utf-8"))
        except Exception: continue
        scenario=str(payload.get("scenarioId") or "")
        mtime=path.stat().st_mtime
        if scenario and (scenario not in result or mtime>result[scenario][0]): result[scenario]=(mtime,path.parent)
    return {k:v[1] for k,v in result.items()}


def source_metadata(source: Path):
    payload=json.loads((source/"grounded_model_input.json").read_text(encoding="utf-8"))
    return str(payload.get("scenarioId") or source.name), str(payload.get("episodeId") or source.name)


async def run_one(source: Path, results_root: Path, model: str, run_number: int, output_root: Path, overwrite: bool):
    scenario,episode=source_metadata(source)
    run_dir=output_root/model_slug(model)/re.sub(r"[^A-Za-z0-9._-]+","-",scenario)/f"run-{run_number}"
    if run_dir.exists():
        if not overwrite: raise FileExistsError(run_dir)
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    base={"schemaVersion":"universal-grounded-run-summary-v2","status":"running","startedAt":now(),"provider":"google_colab_file","model":model,"modelId":model,"scenarioId":scenario,"episodeId":episode,"runNumber":run_number,"sourceDirectory":str(source),"outputDirectory":str(run_dir),"rawResponseDisplayPolicy":"always_show_model_response"}
    summary=run_dir/"run_summary.json"; atomic(summary,base)
    keys=("SLM_PROVIDER","COLAB_RESPONSE_ROOT","COLAB_RESPONSE_RUN","SLM_GROUNDED_RETRY_ENABLED")
    previous={k:os.environ.get(k) for k in keys}
    os.environ.update({"SLM_PROVIDER":"colab_file","COLAB_RESPONSE_ROOT":str(results_root.resolve()),"COLAB_RESPONSE_RUN":str(run_number),"SLM_GROUNDED_RETRY_ENABLED":"false"})
    try:
        result=await rerun_grounded_from_saved_input(episode_dir=source,model_override=model,artifact_dir=run_dir,update_phase7_storage=False)
    except Exception as exc:
        value={**base,"status":"failed","completedAt":now(),"generationSucceeded":False,"displayedInWidget":False,"errorType":type(exc).__name__,"error":str(exc)}
        atomic(summary,value); return value
    finally:
        for k,v in previous.items():
            if v is None: os.environ.pop(k,None)
            else: os.environ[k]=v
    score=result.get("score") or {}; validation=result.get("validation") or {}; reliability=result.get("reliability") or {}; model_meta=result.get("model") or {}
    value={**base,"status":validation.get("groundingStatus") or validation.get("status") or "complete","completedAt":now(),"generationSucceeded":True,"displayedInWidget":True,"strictlyAccepted":bool(validation.get("accepted")),"displayableWithReview":bool(validation.get("displayableWithReview")),"validatorPassed":bool(validation.get("accepted") or validation.get("displayableWithReview")),"totalScore":score.get("total"),"grade":score.get("grade"),"overallPass":score.get("overallPass"),"safetyPass":score.get("safetyPass"),"attemptCount":reliability.get("attemptCount"),"contradictionCount":reliability.get("contradictionCount") or len(validation.get("contradictions") or []),"unsupportedFactCount":reliability.get("unsupportedFactCount") or len(validation.get("unsupportedFacts") or []),"hardErrorCount":len(validation.get("hardErrors") or []),"qualityErrorCount":len(validation.get("qualityErrors") or []),"evidenceCoverageCount":reliability.get("evidenceCoverageCount"),"evidenceCoverageRequired":reliability.get("evidenceCoverageRequired"),"elapsedSeconds":model_meta.get("elapsedSeconds"),"inputTokens":model_meta.get("promptEvalCount"),"outputTokens":model_meta.get("evalCount"),"peakGpuMemoryGiB":model_meta.get("peakGpuMemoryGiB"),"gpuName":model_meta.get("gpuName"),"responseFile":result.get("responseFile")}
    atomic(summary,value); return value


async def run(args):
    results=Path(args.results_root).resolve(); output=Path(args.output_root).resolve()
    sources=[(episode_root()/x).resolve() for x in args.episode_id]
    if args.all_scenarios:
        available=latest_by_scenario(); missing=[s for s in args.scenario_id if s not in available]
        if missing: raise RuntimeError("Missing saved episode(s): "+", ".join(missing))
        sources.extend(available[s] for s in args.scenario_id)
    if not sources: raise ValueError("Use --episode-id or --all-scenarios.")
    for model in args.model:
        for run_number in range(1,args.runs+1):
            for source in sources:
                value=await run_one(source,results,model,run_number,output,args.overwrite)
                print(json.dumps(value,indent=2,ensure_ascii=False),flush=True)
    report=build_report(output)
    (output/"model_selection_report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    write_csv(output/"model_selection_report.csv",report["models"]); write_html(output/"model_selection_report.html",report)


def main():
    scenarios=["VFIB-STEMI-001","TORSADES-LQT-002","VT-ISCHEMIC-003","AFIB-RVR-SEPSIS-004","CHB-HYPERK-005","BRADY-DIGTOX-006","SVT-PSVT-007","NSVT-ECTOPY-008"]
    p=argparse.ArgumentParser(); p.add_argument("--results-root",required=True); p.add_argument("--model",action="append",required=True); p.add_argument("--runs",type=int,default=3); p.add_argument("--episode-id",action="append",default=[]); p.add_argument("--all-scenarios",action="store_true"); p.add_argument("--scenario-id",action="append",default=scenarios); p.add_argument("--output-root",default="data/colab_model_benchmark/evaluated"); p.add_argument("--overwrite",action="store_true"); args=p.parse_args()
    if args.runs<1: raise ValueError("--runs must be >=1")
    asyncio.run(run(args))


if __name__=="__main__": main()
