from __future__ import annotations

from fastapi import (
    APIRouter,
    HTTPException,
)
from pydantic import (
    BaseModel,
    Field,
)

from .config import (
    dataset_root,
    evaluation_enabled,
    model_evaluation_allowed,
    results_root,
    slm_model,
)
from .repository import (
    EvaluationDataError,
    list_episode_ids,
    list_runs,
    load_episode,
    load_index,
    load_run,
)
from .service import (
    run_all,
    run_episode,
)
from .slm_client import (
    EvaluationModelError,
)


router = APIRouter(
    prefix="/api/evaluation",
    tags=["CARDINAL Evaluation"],
)


class RunRequest(BaseModel):
    model: str | None = None
    temperature: float = 0.0
    episodeTimeoutSeconds: float = Field(
        default=420.0,
        ge=30.0,
        le=3600.0,
    )
    resume: bool = False


def require_enabled() -> None:
    if not evaluation_enabled():
        raise HTTPException(
            status_code=404,
            detail=(
                "Evaluation mode is disabled."
            ),
        )


@router.get("/health")
def health():
    require_enabled()

    try:
        ids = list_episode_ids()
        available = True
        error = None

    except EvaluationDataError as exc:
        ids = []
        available = False
        error = str(exc)

    return {
        "enabled": True,
        "modelEvaluationAllowed": (
            model_evaluation_allowed()
        ),
        "datasetAvailable": available,
        "datasetRoot": str(
            dataset_root()
        ),
        "episodeCount": len(ids),
        "resultsPath": str(
            results_root()
        ),
        "answerKeyPubliclyExposed": False,
        "configuredModel": slm_model(),
        "error": error,
    }


@router.get("/episodes")
def episodes():
    require_enabled()

    try:
        return load_index()

    except EvaluationDataError as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc


@router.get(
    "/episodes/{episode_id}"
)
def episode(
    episode_id: str,
):
    require_enabled()

    try:
        return load_episode(
            episode_id
        )

    except EvaluationDataError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc


@router.post(
    "/episodes/{episode_id}/run-slm"
)
async def run_one(
    episode_id: str,
    request: RunRequest,
):
    require_enabled()

    requested_model = (
        request.model
        or slm_model()
    )

    print(
        "[KGEN EVAL API RUN REQUEST]",
        {
            "episodeId": episode_id,
            "requestedModel": (
                requested_model
            ),
            "temperature": (
                request.temperature
            ),
        },
        flush=True,
    )

    try:
        result = await run_episode(
            episode_id=episode_id,
            model_override=request.model,
            temperature=(
                request.temperature
            ),
        )

        print(
            "[KGEN EVAL API RUN COMPLETE]",
            {
                "episodeId": episode_id,
                "runId": result.get(
                    "runId"
                ),
                "model": (
                    result.get(
                        "model",
                        {},
                    ).get("name")
                ),
                "score": (
                    result.get(
                        "score",
                        {},
                    ).get("total")
                ),
                "safetyPass": (
                    result.get(
                        "score",
                        {},
                    ).get(
                        "safetyPass"
                    )
                ),
            },
            flush=True,
        )

        return result

    except PermissionError as exc:
        print(
            "[KGEN EVAL API RUN BLOCKED]",
            {
                "episodeId": episode_id,
                "message": str(exc),
            },
            flush=True,
        )

        raise HTTPException(
            status_code=403,
            detail=str(exc),
        ) from exc

    except EvaluationDataError as exc:
        print(
            "[KGEN EVAL API DATA ERROR]",
            {
                "episodeId": episode_id,
                "message": str(exc),
            },
            flush=True,
        )

        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except EvaluationModelError as exc:
        print(
            "[KGEN EVAL API MODEL ERROR]",
            {
                "episodeId": episode_id,
                "model": requested_model,
                "message": str(exc),
            },
            flush=True,
        )

        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc


@router.post("/run-all")
async def run_everything(
    request: RunRequest,
):
    require_enabled()

    if not model_evaluation_allowed():
        raise HTTPException(
            status_code=403,
            detail=(
                "SLM evaluation is disabled. "
                "Set SLM_EVAL_ALLOW_MODEL=true."
            ),
        )

    return await run_all(
        model_override=request.model,
        temperature=request.temperature,
        episode_timeout_seconds=(
            request.episodeTimeoutSeconds
        ),
        resume=request.resume,
    )


@router.get("/runs")
def runs():
    require_enabled()

    return {
        "runs": list_runs(),
    }


@router.get("/runs/{run_id}")
def run_result(
    run_id: str,
):
    require_enabled()

    try:
        return load_run(
            run_id
        )

    except EvaluationDataError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc
