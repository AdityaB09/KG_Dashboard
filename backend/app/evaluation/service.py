from __future__ import annotations

import asyncio
import secrets
from contextlib import suppress
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Callable

from .config import (
    model_evaluation_allowed,
    slm_model,
)
from .prompt_builder import build_messages
from .repository import (
    EvaluationDataError,
    list_episode_ids,
    list_runs,
    load_answer_key,
    load_episode,
    save_run,
)
from .sanitizer import create_slm_payload
from .scorer import score_response
from .slm_client import call_model


ProgressCallback = Callable[
    [dict[str, Any]],
    None,
]


def _now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _new_run_id() -> str:
    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%d-%H%M%S"
    )

    return (
        f"eval-run-{timestamp}-"
        f"{secrets.token_hex(4)}"
    )


def _evaluation_number(
    episode_id: str,
) -> int:
    episode_ids = list_episode_ids()

    try:
        return (
            episode_ids.index(
                episode_id
            )
            + 1
        )
    except ValueError as exc:
        raise EvaluationDataError(
            f"Unknown episode: {episode_id}"
        ) from exc


def _emit(
    callback: ProgressCallback | None,
    event: dict[str, Any],
) -> None:
    if callback is None:
        return

    try:
        callback(event)
    except Exception:
        # Progress display must never break evaluation.
        return


def _effective_model(
    model_override: str | None,
) -> str:
    return (
        model_override
        or slm_model()
    ).strip()


def _completed_episode_ids(
    model_name: str,
) -> set[str]:
    completed: set[str] = set()

    for run in list_runs():
        if (
            run.get("status")
            == "complete"
            and run.get("model")
            == model_name
            and run.get("episodeId")
        ):
            completed.add(
                str(
                    run["episodeId"]
                )
            )

    return completed


async def run_episode(
    *,
    episode_id: str,
    model_override: str | None = None,
    temperature: float = 0.0,
) -> dict[str, Any]:
    if not model_evaluation_allowed():
        raise PermissionError(
            "SLM evaluation is disabled. "
            "Set SLM_EVAL_ALLOW_MODEL=true."
        )

    episode = load_episode(
        episode_id
    )
    evaluation_number = (
        _evaluation_number(
            episode_id
        )
    )

    sanitized = create_slm_payload(
        episode,
        evaluation_number,
    )
    messages = build_messages(
        sanitized
    )

    model_response, model_metadata = (
        await call_model(
            messages=messages,
            model_override=model_override,
            temperature=temperature,
        )
    )

    run_id = _new_run_id()

    # Save model output before the answer key is loaded.
    pending = {
        "runId": run_id,
        "createdAt": _now_iso(),
        "updatedAt": _now_iso(),
        "status": "model_response_saved",
        "mode": "evaluation",
        "synthetic": True,
        "datasetVersion": episode.get(
            "schemaVersion",
            "episode-slm-eval-v1",
        ),
        "episodeId": episode_id,
        "neutralEpisodeId": sanitized.get(
            "episodeId"
        ),
        "model": model_metadata,
        "modelResponse": model_response,
        "score": None,
    }
    save_run(
        run_id,
        pending,
    )

    answer_key = load_answer_key()
    score = score_response(
        episode_id=episode_id,
        model_response=model_response,
        answer_key=answer_key,
    )

    completed = {
        **pending,
        "updatedAt": _now_iso(),
        "status": "complete",
        "score": score,
    }
    save_run(
        run_id,
        completed,
    )

    return completed


async def _run_episode_with_progress(
    *,
    episode_id: str,
    model_override: str | None,
    temperature: float,
    wall_timeout_seconds: float,
    progress_callback: ProgressCallback | None,
    completed_before: int,
    total: int,
    average_seconds: float | None,
    initial_estimate_seconds: float,
) -> tuple[
    dict[str, Any],
    float,
]:
    started = perf_counter()

    task = asyncio.create_task(
        run_episode(
            episode_id=episode_id,
            model_override=model_override,
            temperature=temperature,
        )
    )

    try:
        while not task.done():
            elapsed = (
                perf_counter()
                - started
            )

            if (
                elapsed
                >= wall_timeout_seconds
            ):
                task.cancel()

                with suppress(
                    asyncio.CancelledError
                ):
                    await task

                raise TimeoutError(
                    "Episode exceeded the hard "
                    "wall-clock limit of "
                    f"{wall_timeout_seconds:.0f} seconds."
                )

            remaining_current = max(
                0.0,
                wall_timeout_seconds
                - elapsed,
            )

            remaining_episodes = max(
                0,
                total
                - completed_before
                - 1,
            )

            estimate_per_episode = (
                average_seconds
                if average_seconds
                else initial_estimate_seconds
            )

            estimated_current_remaining = max(
                0.0,
                estimate_per_episode
                - elapsed,
            )

            estimated_total_remaining = (
                estimated_current_remaining
                + (
                    estimate_per_episode
                    * remaining_episodes
                )
            )

            _emit(
                progress_callback,
                {
                    "type": "episode_tick",
                    "episodeId": episode_id,
                    "completed": completed_before,
                    "total": total,
                    "elapsedSeconds": elapsed,
                    "currentTimeoutRemainingSeconds": (
                        remaining_current
                    ),
                    "estimatedTotalRemainingSeconds": (
                        estimated_total_remaining
                    ),
                    "estimatedSecondsPerEpisode": (
                        estimate_per_episode
                    ),
                    "averageEpisodeSeconds": (
                        average_seconds
                    ),
                },
            )

            await asyncio.wait(
                {task},
                timeout=1.0,
            )

        result = await task
        duration = (
            perf_counter()
            - started
        )
        return result, duration

    except asyncio.CancelledError:
        task.cancel()

        with suppress(
            asyncio.CancelledError
        ):
            await task

        raise


async def run_all(
    *,
    model_override: str | None = None,
    temperature: float = 0.0,
    episode_timeout_seconds: float = 420.0,
    initial_estimate_seconds: float = 300.0,
    resume: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """
    Run all eight CARDINAL cases sequentially.

    episode_timeout_seconds is a true total wall-clock limit
    for each case. This is separate from HTTP read timeouts.

    resume=True skips completed cases for the same model.
    """

    if not model_evaluation_allowed():
        raise PermissionError(
            "SLM evaluation is disabled. "
            "Set SLM_EVAL_ALLOW_MODEL=true."
        )

    episode_ids = list_episode_ids()
    model_name = _effective_model(
        model_override
    )

    already_completed = (
        _completed_episode_ids(
            model_name
        )
        if resume
        else set()
    )

    pending_ids = [
        episode_id
        for episode_id in episode_ids
        if episode_id
        not in already_completed
    ]

    _emit(
        progress_callback,
        {
            "type": "batch_start",
            "total": len(episode_ids),
            "pending": len(pending_ids),
            "skipped": len(
                already_completed
            ),
            "model": model_name,
            "episodeTimeoutSeconds": (
                episode_timeout_seconds
            ),
            "initialEstimateSeconds": (
                initial_estimate_seconds
            ),
        },
    )

    episode_results: list[
        dict[str, Any]
    ] = []

    for episode_id in episode_ids:
        if (
            episode_id
            in already_completed
        ):
            episode_results.append(
                {
                    "episodeId": episode_id,
                    "status": (
                        "skipped_completed"
                    ),
                }
            )

            _emit(
                progress_callback,
                {
                    "type": "episode_skipped",
                    "episodeId": episode_id,
                    "completed": len(
                        episode_results
                    ),
                    "total": len(
                        episode_ids
                    ),
                },
            )

    completed_durations: list[
        float
    ] = []

    completed_count = len(
        already_completed
    )

    for episode_id in pending_ids:
        average_seconds = (
            sum(completed_durations)
            / len(completed_durations)
            if completed_durations
            else None
        )

        _emit(
            progress_callback,
            {
                "type": "episode_start",
                "episodeId": episode_id,
                "completed": completed_count,
                "total": len(
                    episode_ids
                ),
                "averageEpisodeSeconds": (
                    average_seconds
                ),
            },
        )

        started = perf_counter()

        try:
            result, duration = (
                await _run_episode_with_progress(
                    episode_id=episode_id,
                    model_override=model_override,
                    temperature=temperature,
                    wall_timeout_seconds=(
                        episode_timeout_seconds
                    ),
                    progress_callback=(
                        progress_callback
                    ),
                    completed_before=(
                        completed_count
                    ),
                    total=len(
                        episode_ids
                    ),
                    average_seconds=(
                        average_seconds
                    ),
                    initial_estimate_seconds=(
                        initial_estimate_seconds
                    ),
                )
            )

            completed_durations.append(
                duration
            )
            completed_count += 1

            row = {
                "episodeId": episode_id,
                "runId": result[
                    "runId"
                ],
                "status": "complete",
                "durationSeconds": round(
                    duration,
                    2,
                ),
                "score": result[
                    "score"
                ],
            }
            episode_results.append(row)

            _emit(
                progress_callback,
                {
                    "type": "episode_complete",
                    **row,
                    "completed": (
                        completed_count
                    ),
                    "total": len(
                        episode_ids
                    ),
                },
            )

        except TimeoutError as exc:
            duration = (
                perf_counter()
                - started
            )
            completed_count += 1

            row = {
                "episodeId": episode_id,
                "status": "timeout",
                "durationSeconds": round(
                    duration,
                    2,
                ),
                "errorType": (
                    "TimeoutError"
                ),
                "message": str(exc),
            }
            episode_results.append(row)

            _emit(
                progress_callback,
                {
                    "type": "episode_failed",
                    **row,
                    "completed": (
                        completed_count
                    ),
                    "total": len(
                        episode_ids
                    ),
                },
            )

        except Exception as exc:
            duration = (
                perf_counter()
                - started
            )
            completed_count += 1

            row = {
                "episodeId": episode_id,
                "status": "failed",
                "durationSeconds": round(
                    duration,
                    2,
                ),
                "errorType": (
                    type(exc).__name__
                ),
                "message": str(exc),
            }
            episode_results.append(row)

            _emit(
                progress_callback,
                {
                    "type": "episode_failed",
                    **row,
                    "completed": (
                        completed_count
                    ),
                    "total": len(
                        episode_ids
                    ),
                },
            )

    completed_rows = [
        row
        for row in episode_results
        if row.get("status")
        == "complete"
    ]

    scores = [
        row["score"]["total"]
        for row in completed_rows
    ]

    summary = {
        "createdAt": _now_iso(),
        "model": model_name,
        "episodeCount": len(
            episode_ids
        ),
        "completedCount": len(
            completed_rows
        ),
        "skippedCount": sum(
            1
            for row in episode_results
            if row.get("status")
            == "skipped_completed"
        ),
        "timeoutCount": sum(
            1
            for row in episode_results
            if row.get("status")
            == "timeout"
        ),
        "failedCount": sum(
            1
            for row in episode_results
            if row.get("status")
            == "failed"
        ),
        "averageScore": (
            round(
                sum(scores)
                / len(scores),
                2,
            )
            if scores
            else None
        ),
        "averageDurationSeconds": (
            round(
                sum(
                    row[
                        "durationSeconds"
                    ]
                    for row in completed_rows
                )
                / len(
                    completed_rows
                ),
                2,
            )
            if completed_rows
            else None
        ),
        "overallPassCount": sum(
            1
            for row in completed_rows
            if row["score"].get(
                "overallPass"
            )
        ),
        "safetyPassCount": sum(
            1
            for row in completed_rows
            if row["score"].get(
                "safetyPass"
            )
        ),
        "results": episode_results,
    }

    _emit(
        progress_callback,
        {
            "type": "batch_complete",
            "summary": summary,
        },
    )

    return summary