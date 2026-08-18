from __future__ import annotations
import argparse, json, py_compile, re, sys
from pathlib import Path

REQUIRED = [
 'Dockerfile.frontend','Dockerfile.backend','nginx.frontend.conf','.gcloudignore','.dockerignore',
 '.github/workflows/ci.yml','.github/workflows/deploy.yml','.github/workflows/model-control.yml',
 'infra/bootstrap/versions.tf','infra/bootstrap/main.tf','infra/bootstrap/outputs.tf',
 'infra/app/versions.tf','infra/app/locals.tf','infra/app/frontend.tf','infra/app/backend.tf','infra/app/gemma.tf','infra/app/monitoring.tf','infra/app/backend_env.production.json','infra/app/backend_secret_env.production.json',
 'infra/scripts/FIRST_TIME_SETUP.ps1','infra/scripts/DESTROY_APP.ps1','infra/scripts/MODEL_ON.ps1','infra/scripts/MODEL_OFF.ps1',
 'backend/app/cloud_run_auth.py'
]
FORBIDDEN_ENV = re.compile(r'(?i)(password|secret|bearer_token|api_key)$')

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--project-root',default='.'); args=ap.parse_args(); root=Path(args.project_root).resolve()
 p=f=w=0
 def check(ok,label,warn=False):
  nonlocal p,f,w
  if ok: p+=1; print('[PASS]',label)
  elif warn: w+=1; print('[WARN]',label)
  else: f+=1; print('[FAIL]',label)
 print('=== CARDINAL TERRAFORM + CI/CD V1 VERIFIER ===')
 for rel in REQUIRED: check((root/rel).exists(), f'Exists: {rel}')
 check((root/'Dockerfile').exists(), 'Original root Dockerfile remains present')
 check((root/'src/.env').exists(), 'Local src/.env remains present')
 check((root/'backend/.env').exists(), 'Local backend/.env remains present')
 evalp=root/'backend/app/evaluation/slm_client.py'; ph=root/'backend/app/phase7/slm_client.py'
 if evalp.exists():
  t=evalp.read_text(encoding='utf-8',errors='replace'); check('from app.cloud_run_auth import apply_slm_auth' in t and 'await apply_slm_auth' in t,'Evaluation model client has GCP identity seam')
 if ph.exists():
  t=ph.read_text(encoding='utf-8',errors='replace'); check('from app.cloud_run_auth import apply_slm_auth' in t and 'await apply_slm_auth' in t,'Phase7 model client has GCP identity seam')
 envp=root/'infra/app/backend_env.production.json'
 if envp.exists():
  try:
   env=json.loads(envp.read_text(encoding='utf-8-sig'))
   bad=[k for k in env if FORBIDDEN_ENV.search(k) or k in {'SESSION_SECRET_KEY','MONGODB_URI','SMTP_USERNAME','SMTP_PASSWORD'}]
   check(not bad, f'Production JSON excludes secret keys (found={bad})')
   check(env.get('SLM_AUTH_MODE')=='gcp_identity','Production env selects gcp_identity')
   check(not any(str(k).startswith('\ufeff') or str(k).startswith('#') for k in env),'Production JSON has no BOM/comment pseudo-keys')
   sm=json.loads((root/'infra/app/backend_secret_env.production.json').read_text(encoding='utf-8-sig'))
   check(all(k==v for k,v in sm.items()),'Secret map contains names only, never secret values')
  except Exception as e: check(False,f'Production JSON parses: {e}')
 for rel in ['backend/app/cloud_run_auth.py','backend/app/evaluation/slm_client.py','backend/app/phase7/slm_client.py']:
  try: py_compile.compile(str(root/rel),doraise=True); check(True,f'Python compiles: {rel}')
  except Exception as e: check(False,f'Python compiles: {rel} ({e})')
 gem=(root/'infra/app/gemma.tf').read_text(encoding='utf-8',errors='replace') if (root/'infra/app/gemma.tf').exists() else ''
 check('nvidia-rtx-pro-6000' in gem,'Blackwell RTX PRO 6000 configured')
 check('manual_instance_count = 0' in gem,'Gemma safe default is manual 0')
 check('gpu_zonal_redundancy_disabled' in gem,'GPU zonal redundancy disabled for cost control')
 check('max_instance_request_concurrency = 1' in gem,'Gemma concurrency 1')
 check('timeout                          = "3600s"' in gem,'Gemma request timeout 3600s')
 mon=(root/'infra/app/monitoring.tf').read_text(encoding='utf-8',errors='replace') if (root/'infra/app/monitoring.tf').exists() else ''
 check('aditya.bagayatkar09@gmail.com' in (root/'infra/app/variables.tf').read_text(encoding='utf-8',errors='replace'),'Gmail alert target configured')
 check('seconds = 1200' in mon,'20-minute Gemma runtime alert configured')
 check('renotify_interval' in mon,'Runtime alerts re-notify while condition remains open')

 # Separate-origin SMART/browser safety: current project already has credentialed CORS
 # and production SameSite=None+Secure cookies, so no application rewrite is needed.
 main_text=(root/'backend/main.py').read_text(encoding='utf-8',errors='replace') if (root/'backend/main.py').exists() else ''
 oracle_text=(root/'backend/app/oracle_smart.py').read_text(encoding='utf-8',errors='replace') if (root/'backend/app/oracle_smart.py').exists() else ''
 epic_text=(root/'backend/app/epic_smart.py').read_text(encoding='utf-8',errors='replace') if (root/'backend/app/epic_smart.py').exists() else ''
 front_sources='\n'.join(x.read_text(encoding='utf-8',errors='replace') for x in (root/'src').rglob('*.js') if x.is_file()) if (root/'src').exists() else ''
 check('allow_credentials=True' in main_text.replace(' ',''),'Backend CORS allows credentialed frontend requests')
 check('samesite="none" if is_production else "lax"' in oracle_text and 'secure=is_production' in oracle_text,'Oracle SMART cookie is production cross-origin capable')
 check('samesite="none" if is_production else "lax"' in epic_text and 'secure=is_production' in epic_text,'Epic SMART cookie is production cross-origin capable')
 check('credentials: "include"' in front_sources or 'withCredentials: true' in front_sources,'Frontend sends browser credentials to separate backend')
 print(f'\nPASS={p} WARN={w} FAIL={f}')
 print('RESULT=PASS' if f==0 else 'RESULT=FAIL')
 return 0 if f==0 else 1
if __name__=='__main__': raise SystemExit(main())
