from __future__ import annotations

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
)

from app.fhir_cache.service import (
    fhir_cache_service,
)
from app.oracle_smart import (
    get_token_for_request,
)


router = APIRouter(
    prefix="/api/fhir-cache",
    tags=["FHIR Cache"],
)


@router.get("/health")
async def fhir_cache_health():
    return await fhir_cache_service.health()


@router.get("/status")
async def fhir_cache_status(
    patient_id: str = Query(...),
    fhir_base_url: str = Query(...),
):
    return await fhir_cache_service.probe(
        patient_id=patient_id,
        fhir_base_url=fhir_base_url,
    )


@router.get("/current")
async def current_oracle_cache_status(
    request: Request,
):
    token_state = get_token_for_request(
        request
    )

    if not token_state:
        raise HTTPException(
            status_code=401,
            detail=(
                "No Oracle SMART session was found. "
                "Complete Oracle login in this browser first."
            ),
        )

    patient_id = token_state.get(
        "patient_id"
    )

    fhir_base_url = token_state.get(
        "fhir_base_url"
    )

    if not patient_id or not fhir_base_url:
        raise HTTPException(
            status_code=404,
            detail=(
                "The Oracle session does not contain "
                "a patient ID and FHIR base URL."
            ),
        )

    result = await fhir_cache_service.probe(
        patient_id=patient_id,
        fhir_base_url=fhir_base_url,
    )

    return {
        **result,
        "patientId": patient_id,
        "fhirBaseUrl": fhir_base_url,
    }
