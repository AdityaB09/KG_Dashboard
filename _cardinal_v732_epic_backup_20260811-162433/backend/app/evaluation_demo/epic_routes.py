from __future__ import annotations
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from app.evaluation_demo.epic_mapping import EpicEvaluationMappingError
from app.evaluation_demo.epic_service import epic_evaluation_demo_service

router=APIRouter(prefix='/api/epic-evaluation-demo',tags=['epic-evaluation-demo'])
class StartRequest(BaseModel): waveformSessionId:str=Field(min_length=6,max_length=160)

@router.get('/bootstrap')
async def bootstrap(request:Request):
    try: return await epic_evaluation_demo_service.bootstrap(request)
    except EpicEvaluationMappingError as error: raise HTTPException(status_code=409,detail=str(error)) from error

@router.post('/start')
async def start(request:Request,payload:StartRequest):
    try: return await epic_evaluation_demo_service.start(request,waveform_session_id=payload.waveformSessionId)
    except EpicEvaluationMappingError as error: raise HTTPException(status_code=409,detail=str(error)) from error
    except (PermissionError,FileNotFoundError) as error: raise HTTPException(status_code=404,detail=str(error)) from error
    except (ValueError,RuntimeError) as error: raise HTTPException(status_code=409,detail=str(error)) from error

@router.get('/status/{waveform_session_id}')
async def status(waveform_session_id:str): return epic_evaluation_demo_service.status(waveform_session_id)

@router.post('/cancel/{waveform_session_id}')
async def cancel(waveform_session_id:str):
    try: return epic_evaluation_demo_service.cancel(waveform_session_id)
    except FileNotFoundError as error: raise HTTPException(status_code=404,detail=str(error)) from error
    except RuntimeError as error: raise HTTPException(status_code=409,detail=str(error)) from error

@router.get('/mapping-status')
async def mapping_status(request:Request):
    from app.epic_smart import get_epic_token_for_request
    from app.evaluation_demo.epic_mapping import resolve_epic_patient_plan
    token=get_epic_token_for_request(request)
    if not token: raise HTTPException(status_code=401,detail='Epic SMART session unavailable.')
    pid=str(token.get('patient_id') or '').strip()
    if not pid: raise HTTPException(status_code=400,detail='Epic token has no patient context.')
    plan=resolve_epic_patient_plan(patient_id=pid,selection_key=str(token.get('smart_session_id') or ''))
    return {'patientIdFromToken':pid,'encounterIdFromToken':token.get('encounter_id'),'mapping':plan}
