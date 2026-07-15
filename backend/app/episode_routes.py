from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.episodes import episode_coordinator


router = APIRouter(
    prefix="/api/episodes",
    tags=["episodes"],
)


@router.get("")
async def list_episodes():
    episodes = episode_coordinator.list_episodes()

    return {
        "count": len(episodes),
        "episodes": episodes,
    }


@router.get("/latest")
async def latest_episode():
    return {
        "episode": (
            episode_coordinator.get_latest_episode()
        )
    }


@router.get("/events")
async def episode_events(
    request: Request,
):
    queue = episode_coordinator.subscribe()

    async def generator():
        try:
            connected = {
                "type": "episode.connected",
            }

            yield "event: episode.connected\n"
            yield (
                f"data: "
                f"{json.dumps(connected)}\n\n"
            )

            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=15,
                    )

                    event_type = event.get(
                        "type",
                        "episode.event",
                    )

                    yield f"event: {event_type}\n"
                    yield (
                        f"data: "
                        f"{json.dumps(event, separators=(',', ':'))}"
                        f"\n\n"
                    )

                except asyncio.TimeoutError:
                    heartbeat = {
                        "type": "episode.heartbeat",
                    }

                    yield "event: episode.heartbeat\n"
                    yield (
                        f"data: "
                        f"{json.dumps(heartbeat)}\n\n"
                    )

        finally:
            episode_coordinator.unsubscribe(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{episode_id}/waveforms")
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
            for item in leads.split(",")
            if item.strip()
        ]

        return episode_coordinator.get_waveforms(
            episode_id,
            requested_leads=requested_leads,
            max_points=max_points,
        )

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Episode not found.",
        )


@router.get("/{episode_id}/context")
async def episode_context(
    episode_id: str,
):
    try:
        return episode_coordinator.get_context(
            episode_id
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Episode context not found.",
        )


@router.get("/{episode_id}/analysis")
async def episode_analysis(
    episode_id: str,
):
    try:
        return episode_coordinator.get_analysis(
            episode_id
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Episode analysis not found.",
        )


@router.post("/{episode_id}/analyze")
async def analyze_episode(
    episode_id: str,
):
    try:
        episode_coordinator.get_episode(episode_id)

        return {
            "episodeId": episode_id,
            "status": "pending",
            "message": (
                "Signal analysis is introduced "
                "in the next phase."
            ),
        }

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Episode not found.",
        )


@router.get("/{episode_id}")
async def episode_details(
    episode_id: str,
):
    try:
        return episode_coordinator.get_episode(
            episode_id
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Episode not found.",
        )