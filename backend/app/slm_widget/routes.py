from fastapi import APIRouter, HTTPException

from app.slm_widget.assembler import slm_widget_assembler
from app.slm_widget.model_registry import MODEL_REGISTRY


router = APIRouter(
    prefix="/api/slm-widget",
    tags=["SLM Widget"],
)


@router.get("/models")
async def slm_widget_models():
    return {"models": list(MODEL_REGISTRY.values())}


@router.get("/incidents/{incident_id}")
async def incident_slm_widget(incident_id: str):
    try:
        return slm_widget_assembler.assemble(
            incident_id=incident_id
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Incident not found.",
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        )
