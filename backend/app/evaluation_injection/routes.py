from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.evaluation_injection.scenario_catalog import (
    list_scenario_descriptors,
)
from app.evaluation_injection.service import (
    evaluation_injection_service,
)
from app.evaluation_injection.precomputed_response_repository import (
    precomputed_demo_status,
)


router = APIRouter(
    prefix="/api/evaluation-injection",
    tags=["evaluation-injection"],
)


class ArmRequest(BaseModel):
    scenarioId: str = "VT-ISCHEMIC-003"
    baselineSeconds: float = Field(default=10.0, ge=1.0, le=60.0)
    preSeconds: float = Field(default=6.0, ge=1.0, le=30.0)
    postSeconds: float = Field(default=6.0, ge=1.0, le=30.0)
    runSlm: bool = True


def _dataset_root():
    return evaluation_injection_service._loader.dataset_root()


@router.get("/health")
async def health():
    scenarios = list_scenario_descriptors(
        settings.EVALUATION_INJECTION_ALLOWED_SCENARIOS,
        dataset_root=_dataset_root(),
    )

    return {
        "enabled": evaluation_injection_service.enabled,
        "allowedScenarios": settings.EVALUATION_INJECTION_ALLOWED_SCENARIOS,
        "scenarioCount": len(scenarios),
        "availableScenarioCount": sum(1 for item in scenarios if item.get("available")),
        "defaultBaselineSeconds": settings.EVALUATION_INJECTION_BASELINE_SECONDS,
        "defaultPreSeconds": settings.EVALUATION_INJECTION_PRE_SECONDS,
        "defaultPostSeconds": settings.EVALUATION_INJECTION_POST_SECONDS,
        "detectorRateThreshold": settings.EVALUATION_INJECTION_VT_RATE_THRESHOLD,
        "detectorQrsThresholdMs": settings.EVALUATION_INJECTION_QRS_THRESHOLD_MS,
    }


@router.get("/precomputed-responses")
async def precomputed_responses():
    """Deployment readiness for the eight offline MedGemma demo responses."""
    return precomputed_demo_status()


@router.get("/scenarios")
async def scenarios():
    if not evaluation_injection_service.enabled:
        raise HTTPException(status_code=404, detail="Evaluation injection is disabled.")

    records = list_scenario_descriptors(
        settings.EVALUATION_INJECTION_ALLOWED_SCENARIOS,
        dataset_root=_dataset_root(),
    )

    return {
        "schemaVersion": "evaluation-injection-scenarios-v2",
        "count": len(records),
        "scenarios": records,
    }


@router.post("/sessions/{session_id}/arm")
async def arm(session_id: str, request: ArmRequest):
    try:
        return evaluation_injection_service.arm(
            session_id=session_id,
            scenario_id=request.scenarioId,
            baseline_seconds=request.baselineSeconds,
            pre_seconds=request.preSeconds,
            post_seconds=request.postSeconds,
            run_slm=request.runSlm,
        )
    except PermissionError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/sessions/{session_id}")
async def status(session_id: str):
    return evaluation_injection_service.status(session_id)


@router.post("/sessions/{session_id}/cancel")
async def cancel(session_id: str):
    try:
        return evaluation_injection_service.cancel(session_id)
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail="Evaluation injection session was not found.",
        ) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
