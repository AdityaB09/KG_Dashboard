from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.evaluation_demo.mapping import OracleEvaluationMappingError
from app.evaluation_demo.service import oracle_evaluation_demo_service


router = APIRouter(
    prefix="/api/evaluation-demo",
    tags=["oracle-evaluation-demo"],
)


class StartRequest(BaseModel):
    waveformSessionId: str = Field(min_length=6, max_length=160)


@router.get("/bootstrap")
async def bootstrap(request: Request):
    try:
        return await oracle_evaluation_demo_service.bootstrap(request)
    except OracleEvaluationMappingError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/start")
async def start(request: Request, payload: StartRequest):
    try:
        return await oracle_evaluation_demo_service.start(
            request,
            waveform_session_id=payload.waveformSessionId,
        )
    except OracleEvaluationMappingError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except PermissionError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/status/{waveform_session_id}")
async def status(waveform_session_id: str):
    return oracle_evaluation_demo_service.status(waveform_session_id)


@router.post("/cancel/{waveform_session_id}")
async def cancel(waveform_session_id: str):
    try:
        return oracle_evaluation_demo_service.cancel(waveform_session_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
