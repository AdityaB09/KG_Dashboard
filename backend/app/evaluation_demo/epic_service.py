from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from fastapi import Request

from app.config import settings
from app.epic_smart import get_epic_token_for_request
from app.evaluation_demo.epic_mapping import resolve_epic_patient_plan
from app.evaluation_injection.service import evaluation_injection_service

ACTIVE_STATES={"ARMED","INJECTING","POST_EVENT","ANALYZING"}
TERMINAL_STATES={"COMPLETE","FAILED","CANCELLED"}


def _now_iso(): return datetime.now(timezone.utc).isoformat()


def _launch_identity(patient_id: str) -> dict[str,Any]:
    return {"id":patient_id,"fhirId":patient_id,"name":"Epic SMART launch","display":"Epic SMART launch","mrn":patient_id,"source":"epic_smart_token","clinicalContextUsed":False}


def _episode_pack_summary(scenario_id: str) -> dict[str,Any]:
    configured=Path(settings.EVALUATION_INJECTION_DATASET_ROOT)
    if not configured.is_absolute(): configured=(Path(__file__).resolve().parents[2]/configured).resolve()
    record=json.loads((configured/'episodes'/f'{scenario_id}.json').read_text(encoding='utf-8'))
    patient=dict(record.get('patient') or {}); patient.pop('disclaimer',None)
    return {
      "scenarioId":scenario_id,"schemaVersion":record.get('schemaVersion') or 'episode-slm-eval-v1',
      "patient":patient,"episode":dict(record.get('episode') or {}),"vitals":dict(record.get('vitals') or {}),
      "labs":dict(record.get('labs') or {}),"medications":list(record.get('medications') or []) if isinstance(record.get('medications'),list) else [],
      "clinicalContext":dict(record.get('clinicalContext') or {}),
      "ecg":{"sampleRate":(record.get('ecg') or {}).get('sampleRate'),"durationSeconds":(record.get('ecg') or {}).get('durationSeconds'),"measurements":dict((record.get('ecg') or {}).get('measurements') or {})},
      "clinicalContextSource":"complete_episode_pack","epicFhirContextUsed":False,
    }


def _smart_session_key(token_state: dict[str,Any]) -> str:
    existing=str(token_state.get('smart_session_id') or token_state.get('_epic_demo_session_key') or '').strip()
    if existing: return existing
    generated=secrets.token_urlsafe(18); token_state['_epic_demo_session_key']=generated; return generated


class EpicEvaluationDemoService:
    def __init__(self):
        self._lock=RLock(); self._start_lock=asyncio.Lock(); self._runs={}

    def _run_for_smart_session(self,key):
        with self._lock: runs=list(self._runs.values())
        for run in reversed(runs):
            if str(run.get('smartSessionKey') or '')==key: return dict(run)
        return None

    def _run_for_waveform_session(self,waveform_session_id):
        with self._lock:
            run=self._runs.get(waveform_session_id); return dict(run) if run else None

    @staticmethod
    def _status(waveform_session_id):
        try:
            value=evaluation_injection_service.status(waveform_session_id)
            return dict(value) if isinstance(value,dict) else {'state':'IDLE'}
        except Exception: return {'state':'IDLE'}

    async def bootstrap(self,request:Request):
        if not settings.EPIC_EVALUATION_DEMO_ENABLED:
            return {'ready':False,'enabled':False,'reason':'epic_evaluation_demo_disabled'}
        if not evaluation_injection_service.enabled:
            return {'ready':False,'enabled':True,'reason':'evaluation_injection_disabled'}
        token_state=get_epic_token_for_request(request)
        if not token_state: return {'ready':False,'enabled':True,'reason':'epic_smart_session_missing'}
        patient_id=str(token_state.get('patient_id') or '').strip()
        if not patient_id: return {'ready':False,'enabled':True,'reason':'epic_patient_context_missing'}
        key=_smart_session_key(token_state)
        plan=resolve_epic_patient_plan(patient_id=patient_id,selection_key=key)
        pack=_episode_pack_summary(plan['scenarioId'])
        existing=self._run_for_smart_session(key)
        existing_run=None
        if existing:
            status=self._status(str(existing.get('waveformSessionId') or ''))
            existing_run={**status,'reused':True,'waveformSessionId':existing.get('waveformSessionId'),'demoRunId':existing.get('demoRunId')}
        return {
          'ready':True,'enabled':True,'mode':'epic_evaluation_auto','provider':'epic',
          'patient':_launch_identity(patient_id),'episodePack':pack,'clinicalContextMode':'episode_pack_only',
          'epicFhirContextUsed':False,'patientResourceLoaded':False,'encounterId':token_state.get('encounter_id'),
          'scenario':plan,'existingRun':existing_run,
        }

    async def start(self,request:Request,*,waveform_session_id:str):
        async with self._start_lock:
            token_state=get_epic_token_for_request(request)
            if not token_state: raise RuntimeError('Epic SMART session is unavailable.')
            key=_smart_session_key(token_state)
            existing=self._run_for_smart_session(key)
            if existing:
                wid=str(existing.get('waveformSessionId') or waveform_session_id)
                current=self._status(wid)
                return {**current,'reused':True,'mode':'epic_evaluation_auto','epicDemo':existing.get('epicDemo') or current.get('epicDemo')}
            bootstrap=await self.bootstrap(request)
            if not bootstrap.get('ready'): raise RuntimeError(str(bootstrap.get('reason') or 'Epic evaluation demo is not ready.'))
            current=self._status(waveform_session_id); state=str(current.get('state') or 'IDLE').upper()
            if state in ACTIVE_STATES: return {**current,'reused':True,'mode':'epic_evaluation_auto'}
            plan=bootstrap['scenario']; patient=bootstrap['patient']; demo_id='epic-eval-'+secrets.token_urlsafe(12)
            epic_demo={
              'mode':'epic_evaluation_auto','demoRunId':demo_id,'patientId':patient['id'],'encounterId':bootstrap.get('encounterId'),
              'launchPatientId':patient['id'],'launchIdentity':_launch_identity(patient['id']),
              'episodePackPatient':bootstrap['episodePack'].get('patient'),'clinicalContextMode':'episode_pack_only',
              'clinicalContextSource':'complete_episode_pack','epicFhirContextUsed':False,'scenarioId':plan['scenarioId'],
              'mappingSource':plan.get('mappingSource'),'mappingNote':plan.get('mappingNote'),'exactMapping':plan.get('exactMapping'),
              'startedAt':_now_iso(),
            }
            armed=evaluation_injection_service.arm(
              session_id=waveform_session_id,scenario_id=plan['scenarioId'],baseline_seconds=plan['baselineSeconds'],
              pre_seconds=plan['preSeconds'],post_seconds=plan['postSeconds'],run_slm=plan['runSlm'],
              epic_demo=epic_demo,token_override=None,
            )
            with self._lock:
                self._runs[waveform_session_id]={'demoRunId':demo_id,'waveformSessionId':waveform_session_id,'smartSessionKey':key,'epicDemo':epic_demo,'createdAt':_now_iso()}
            return {**armed,'mode':'epic_evaluation_auto','epicDemo':epic_demo,'episodePack':bootstrap['episodePack'],'clinicalContextMode':'episode_pack_only','epicFhirContextUsed':False,'reused':False}

    def status(self,waveform_session_id):
        return {**self._status(waveform_session_id),'mode':'epic_evaluation_auto','demoRun':self._run_for_waveform_session(waveform_session_id)}

    def cancel(self,waveform_session_id): return evaluation_injection_service.cancel(waveform_session_id)


epic_evaluation_demo_service=EpicEvaluationDemoService()
