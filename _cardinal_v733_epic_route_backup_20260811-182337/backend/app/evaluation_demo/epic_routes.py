from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.epic_smart import get_epic_token_for_request
from app.evaluation_demo.epic_mapping import EpicEvaluationMappingError, resolve_epic_patient_plan
from app.evaluation_demo.epic_service import epic_evaluation_demo_service

router = APIRouter(prefix="/api/epic-evaluation-demo", tags=["epic-evaluation-demo"])


class StartRequest(BaseModel):
    waveformSessionId: str = Field(min_length=6, max_length=160)


@router.get("/bootstrap")
async def bootstrap(request: Request):
    try:
        return await epic_evaluation_demo_service.bootstrap(request)
    except EpicEvaluationMappingError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/start")
async def start(request: Request, payload: StartRequest):
    try:
        return await epic_evaluation_demo_service.start(
            request,
            waveform_session_id=payload.waveformSessionId,
        )
    except EpicEvaluationMappingError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (PermissionError, FileNotFoundError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/status/{waveform_session_id}")
async def status(waveform_session_id: str):
    return epic_evaluation_demo_service.status(waveform_session_id)


@router.post("/cancel/{waveform_session_id}")
async def cancel(waveform_session_id: str):
    try:
        return epic_evaluation_demo_service.cancel(waveform_session_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/mapping-status")
async def mapping_status(request: Request):
    token = get_epic_token_for_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Epic SMART session unavailable.")

    patient_id = str(token.get("patient_id") or "").strip()
    patient_key = str(token.get("patient_key") or "").strip()
    if not patient_id or not patient_key or not token.get("patient_verified"):
        raise HTTPException(status_code=400, detail="Epic session does not contain a verified patient context.")

    plan = resolve_epic_patient_plan(
        patient_key=patient_key,
        selection_key=str(token.get("smart_session_id") or ""),
    )
    return {
        "provider": "epic",
        "recognizedSandboxPatient": True,
        "patientIdFromToken": patient_id,
        "patientKey": patient_key,
        "patientDisplayName": token.get("patient_display_name"),
        "patientIdVerified": True,
        "encounterIdFromToken": token.get("encounter_id"),
        "mappingSource": "epic-smart-launch-context+patient-read",
        "scenarioSelectionSource": "explicit-config",
        "mapping": plan,
    }
