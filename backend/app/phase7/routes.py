from __future__ import annotations

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
)

from app.oracle_smart import (
    get_token_for_request,
)
from app.phase7.config import (
    phase7_settings,
)
from app.phase7.orchestrator import (
    phase7_orchestrator,
)


router = APIRouter(
    prefix="/api/phase7",
    tags=["phase7"],
)


@router.get("/health")
async def phase7_health():
    return {
        "ok": True,
        "schemaVersion": (
            phase7_settings
            .schema_version
        ),
        "enabled": (
            phase7_settings.enabled
        ),
        "autoRunAfterCapture": (
            phase7_settings
            .auto_run_after_capture
        ),
        "loadClinicalContext": (
            phase7_settings
            .load_clinical_context
        ),
        "runSlmAutomatically": (
            phase7_settings
            .run_slm_automatically
        ),
        "slmEnabled": (
            phase7_settings
            .slm_enabled
        ),
    }


@router.post(
    "/incidents/{incident_id}/run"
)
async def run_phase7_incident(
    incident_id: str,
    request: Request,
    force: bool = Query(
        default=False
    ),
    force_context: bool = Query(
        default=False
    ),
    run_slm: bool = Query(
        default=False
    ),
    patient_id: str | None = Query(
        default=None
    ),
):
    token_state = (
        get_token_for_request(
            request
        )
    )

    result = await (
        phase7_orchestrator
        .run_incident(
            incident_id=(
                incident_id
            ),
            force=force,
            force_context=(
                force_context
            ),
            run_model=run_slm,
            token_override=(
                token_state
            ),
            requested_patient_id=(
                patient_id
            ),
        )
    )

    if result.get("error"):
        raise HTTPException(
            status_code=500,
            detail=result["error"],
        )

    return result


@router.get(
    "/incidents/{incident_id}/status"
)
async def phase7_incident_status(
    incident_id: str,
):
    try:
        return (
            phase7_orchestrator
            .get_status(
                incident_id
            )
        )

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                "Phase 7 status has not "
                "been created yet."
            ),
        )


@router.get(
    "/incidents/{incident_id}/evidence"
)
async def phase7_incident_evidence(
    incident_id: str,
):
    try:
        return (
            phase7_orchestrator
            .get_evidence(
                incident_id
            )
        )

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                "Phase 7 evidence has not "
                "been created yet."
            ),
        )


@router.get(
    "/incidents/{incident_id}/prompt"
)
async def phase7_incident_prompt(
    incident_id: str,
):
    try:
        return (
            phase7_orchestrator
            .get_prompt(
                incident_id
            )
        )

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                "Phase 7 prompt has not "
                "been created yet."
            ),
        )


@router.get(
    "/incidents/{incident_id}/slm-response"
)
async def phase7_incident_slm_response(
    incident_id: str,
):
    try:
        return (
            phase7_orchestrator
            .get_slm_response(
                incident_id
            )
        )

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                "No SLM response has been "
                "stored for this incident."
            ),
        )
