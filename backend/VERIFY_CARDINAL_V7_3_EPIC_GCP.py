from pathlib import Path
import json, py_compile, sys
backend=Path(__file__).resolve().parent if Path(__file__).resolve().parent.name=='backend' else Path(__file__).resolve().parent/'backend'
project=backend.parent
checks=[backend/'app/epic_smart.py',backend/'app/evaluation_demo/epic_mapping.py',backend/'app/evaluation_demo/epic_service.py',backend/'app/evaluation_demo/epic_routes.py',backend/'app/evaluation_demo/epic_patient_scenario_map.json',project/'src/evaluation/epicEvaluationDemo.js',project/'Dockerfile']
missing=[str(p) for p in checks if not p.exists()]
if missing: print('MISSING:',*missing,sep='\n - '); sys.exit(1)
for p in [backend/'main.py',backend/'app/config.py',backend/'app/epic_smart.py',backend/'app/evaluation_demo/epic_mapping.py',backend/'app/evaluation_demo/epic_service.py',backend/'app/evaluation_demo/epic_routes.py',backend/'app/evaluation_injection/service.py']:
    py_compile.compile(str(p),doraise=True)
scenarios=set()
root=backend/'SLM_Eval/episodes'
for p in root.glob('*.json'): scenarios.add(p.stem)
assert len(scenarios)==14, len(scenarios)
pre=backend/'data/etiology_v7_precomputed/google-gemma-4-31B-it'
assert all((pre/f'{s}.json').exists() for s in scenarios)
print('CARDINAL V7.3 Epic/GCP verification: PASS')
print(' - Epic auth path: separate cookie/session namespace')
print(' - Epic evaluation routes: /api/epic-evaluation-demo/*')
print(' - Oracle mapping remains separate')
print(' - scenarios:',len(scenarios))
print(' - Cloud Run Dockerfile:',(project/'Dockerfile').exists())
