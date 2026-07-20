from __future__ import annotations

import asyncio
import json
import traceback

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import (
    StreamingResponse,
)

from app.analysis.episode_analyzer import (
    episode_analyzer,
)
from app.analysis.incident_analyzer import (
    incident_analyzer,
)
from app.analysis.models import (
    AnalysisInputError,
)
from app.analysis.slm_context import (
    build_phase6_slm_context,
)
from app.clinical_context import (
    clinical_context_service,
)
from app.config import settings
from app.episodes import (
    episode_coordinator,
)
from app.incidents import (
    incident_coordinator,
)
from app.oracle_smart import (
    get_token_for_request,
)


router = APIRouter(
    prefix="/api/episodes",
    tags=["episodes"],
)

incident_router = APIRouter(
    prefix="/api/incidents",
    tags=["incidents"],
)


@router.get("")
async def list_episodes():
    episodes = (
        episode_coordinator
        .list_episodes()
    )

    return {
        "count": len(episodes),
        "episodes": episodes,
    }


@router.get("/latest")
async def latest_episode():
    return {
        "episode": (
            episode_coordinator
            .get_latest_episode()
        )
    }


@router.get("/events")
async def episode_events(
    request: Request,
):
    queue = (
        episode_coordinator
        .subscribe()
    )

    async def generator():
        try:
            connected = {
                "type": (
                    "episode.connected"
                ),
            }

            yield (
                "event: "
                "episode.connected\n"
            )

            yield (
                "data: "
                f"{json.dumps(connected)}"
                "\n\n"
            )

            while True:
                if (
                    await request
                    .is_disconnected()
                ):
                    break

                try:
                    event = (
                        await asyncio
                        .wait_for(
                            queue.get(),
                            timeout=15,
                        )
                    )

                    event_type = (
                        event.get(
                            "type",
                            "episode.event",
                        )
                    )

                    yield (
                        f"event: "
                        f"{event_type}\n"
                    )

                    yield (
                        "data: "
                        f"{json.dumps(event, separators=(',', ':'))}"
                        "\n\n"
                    )

                except (
                    asyncio.TimeoutError
                ):
                    heartbeat = {
                        "type": (
                            "episode.heartbeat"
                        ),
                    }

                    yield (
                        "event: "
                        "episode.heartbeat\n"
                    )

                    yield (
                        "data: "
                        f"{json.dumps(heartbeat)}"
                        "\n\n"
                    )

        finally:
            episode_coordinator.unsubscribe(
                queue
            )

    return StreamingResponse(
        generator(),
        media_type=(
            "text/event-stream"
        ),
        headers={
            "Cache-Control": (
                "no-cache"
            ),
            "Connection": (
                "keep-alive"
            ),
            "X-Accel-Buffering": (
                "no"
            ),
        },
    )


@router.get(
    "/incart/annotation-summary"
)
async def incart_annotation_summary():
    return await asyncio.to_thread(
        episode_coordinator
        .get_annotation_summary
    )


@router.get(
    "/{episode_id}/waveforms"
)
async def episode_waveforms(
    episode_id: str,
    leads: str = Query(
        default="lead2,lead1,avf"
    ),
    max_points: int = Query(
        default=1800,
        ge=100,
        le=10000,
    ),
):
    try:
        requested_leads = [
            item.strip()
            for item
            in leads.split(",")
            if item.strip()
        ]

        return (
            episode_coordinator
            .get_waveforms(
                episode_id,
                requested_leads=(
                    requested_leads
                ),
                max_points=max_points,
            )
        )

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                "Episode not found."
            ),
        )


@router.get(
    "/{episode_id}/context"
)
async def episode_context(
    episode_id: str,
):
    try:
        return (
            episode_coordinator
            .get_context(
                episode_id
            )
        )

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                "Episode context "
                "not found."
            ),
        )


@router.get(
    "/{episode_id}/analysis"
)
async def episode_analysis(
    episode_id: str,
):
    try:
        return await asyncio.to_thread(
            episode_analyzer.get,
            episode_id,
        )

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                "Episode not found."
            ),
        )

    except AnalysisInputError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "errorType": (
                    type(
                        error
                    ).__name__
                ),
                "message": str(error),
                "episodeId": (
                    episode_id
                ),
                "details": (
                    error.details
                ),
            },
        )


@router.post(
    "/{episode_id}/analyze"
)
async def analyze_episode(
    episode_id: str,
    force: bool = Query(
        default=False
    ),
):
    try:
        result = (
            await asyncio.to_thread(
                episode_analyzer
                .analyze,
                episode_id,
                force=force,
            )
        )

        episode_coordinator.publish(
            {
                "type": (
                    "episode.analysis_ready"
                ),
                "episodeId": (
                    episode_id
                ),
                "status": (
                    result.get(
                        "status"
                    )
                ),
                "algorithmVersion": (
                    result.get(
                        "algorithmVersion"
                    )
                ),
            }
        )

        return result

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                "Episode not found."
            ),
        )

    except AnalysisInputError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "errorType": (
                    type(
                        error
                    ).__name__
                ),
                "message": str(error),
                "episodeId": (
                    episode_id
                ),
                "details": (
                    error.details
                ),
            },
        )

    except Exception as error:
        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail={
                "errorType": (
                    type(
                        error
                    ).__name__
                ),
                "message": str(error),
                "episodeId": (
                    episode_id
                ),
            },
        )


@router.get("/{episode_id}")
async def episode_details(
    episode_id: str,
):
    try:
        return (
            episode_coordinator
            .get_episode(
                episode_id
            )
        )

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                "Episode not found."
            ),
        )


@incident_router.get("")
async def list_incidents():
    incidents = (
        incident_coordinator
        .list_incidents()
    )

    return {
        "count": len(incidents),
        "incidents": incidents,
    }


@incident_router.get("/latest")
async def latest_incident():
    return {
        "incident": (
            incident_coordinator
            .get_latest_incident()
        )
    }


@incident_router.post("/rebuild")
async def rebuild_incidents():
    return await asyncio.to_thread(
        incident_coordinator
        .rebuild_from_episodes
    )


@incident_router.get(
    "/{incident_id}/episodes"
)
async def incident_episodes(
    incident_id: str,
):
    try:
        episodes = (
            incident_coordinator
            .get_incident_episodes(
                incident_id
            )
        )

        return {
            "count": len(episodes),
            "episodes": episodes,
        }

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                "Incident not found."
            ),
        )


@incident_router.get(
    "/{incident_id}/analysis"
)
async def incident_analysis(
    incident_id: str,
):
    try:
        return await asyncio.to_thread(
            incident_analyzer.get,
            incident_id,
        )

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                "Incident not found."
            ),
        )


@incident_router.post(
    "/{incident_id}/analyze"
)
async def analyze_incident(
    incident_id: str,
    force: bool = Query(
        default=False
    ),
):
    try:
        return await asyncio.to_thread(
            incident_analyzer.analyze,
            incident_id,
            force=force,
        )

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                "Incident not found."
            ),
        )

    except AnalysisInputError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "errorType": (
                    type(
                        error
                    ).__name__
                ),
                "message": str(error),
                "incidentId": (
                    incident_id
                ),
                "details": (
                    error.details
                ),
            },
        )

    except Exception as error:
        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail={
                "errorType": (
                    type(
                        error
                    ).__name__
                ),
                "message": str(error),
                "incidentId": (
                    incident_id
                ),
            },
        )


@incident_router.get(
    "/{incident_id}/slm-context"
)
async def incident_slm_context(
    incident_id: str,
):
    try:
        return await asyncio.to_thread(
            build_phase6_slm_context,
            incident_id,
        )

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                "Incident not found."
            ),
        )


@incident_router.get(
    "/{incident_id}/context"
)
async def incident_context(
    incident_id: str,
):
    try:
        return (
            clinical_context_service
            .get(
                incident_id
            )
        )

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                "Incident not found."
            ),
        )


@incident_router.post(
    "/{incident_id}/context/load"
)
async def load_incident_context(
    incident_id: str,
    request: Request,
    patient_id: str | None = Query(
        default=None
    ),
):
    try:
        token_state = (
            get_token_for_request(
                request
            )
        )

        effective_patient_id = (
            patient_id
            or (
                token_state.get(
                    "patient_id"
                )
                if token_state
                else None
            )
            or (
                settings
                .ORACLE_TEST_PATIENT_ID
            )
            or None
        )

        return await (
            clinical_context_service
            .load(
                incident_id=(
                    incident_id
                ),
                patient_id=(
                    effective_patient_id
                ),
                access_token=(
                    token_state.get(
                        "access_token"
                    )
                    if token_state
                    else None
                ),
                fhir_base_url=(
                    token_state.get(
                        "fhir_base_url"
                    )
                    if token_state
                    else None
                ),
            )
        )

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                "Incident not found."
            ),
        )

    except Exception as error:
        print(
            (
                "[KGEN CLINICAL "
                "CONTEXT LOAD ERROR]"
            ),
            type(error).__name__,
            str(error),
        )

        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail={
                "errorType": (
                    type(
                        error
                    ).__name__
                ),
                "message": str(error),
                "incidentId": (
                    incident_id
                ),
            },
        )


@incident_router.get(
    "/{incident_id}"
)
async def incident_details(
    incident_id: str,
):
    try:
        return (
            incident_coordinator
            .get_incident(
                incident_id
            )
        )

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                "Incident not found."
            ),
        )