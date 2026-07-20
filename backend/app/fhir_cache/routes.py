from fastapi import APIRouter

from app.fhir_cache.service import fhir_cache_service

router = APIRouter(
    prefix="/api/fhir-cache",
    tags=["FHIR Cache"],
)


@router.get("/health")
async def fhir_cache_health():
    return await fhir_cache_service.health()
