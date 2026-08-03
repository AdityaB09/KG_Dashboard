from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import (
    dataset_root,
    results_root,
)


class EvaluationDataError(RuntimeError):
    pass


_EPISODE_ID_RE = re.compile(
    r"^[A-Z0-9][A-Z0-9_-]{2,80}$"
)
_RUN_ID_RE = re.compile(
    r"^eval-run-[A-Za-z0-9_-]{6,120}$"
)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise EvaluationDataError(
            f"Evaluation file not found: {path}"
        )

    try:
        value = json.loads(
            path.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as exc:
        raise EvaluationDataError(
            f"Invalid JSON in {path.name}: {exc}"
        ) from exc
    except OSError as exc:
        raise EvaluationDataError(
            f"Could not read {path}: {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise EvaluationDataError(
            f"{path.name} must contain a JSON object."
        )

    return value


def validate_episode_id(
    episode_id: str,
) -> str:
    safe_id = str(episode_id).strip()

    if not _EPISODE_ID_RE.fullmatch(safe_id):
        raise EvaluationDataError(
            "Invalid evaluation episode ID."
        )

    return safe_id


def load_index() -> dict[str, Any]:
    return _read_json(
        dataset_root() / "index.json"
    )


def list_episode_ids() -> list[str]:
    manifest = load_index()
    ids: list[str] = []

    for item in manifest.get("episodes", []):
        if not isinstance(item, dict):
            continue

        episode_id = item.get("episodeId")
        if isinstance(episode_id, str):
            ids.append(
                validate_episode_id(
                    episode_id
                )
            )

    return ids


def load_episode(
    episode_id: str,
) -> dict[str, Any]:
    safe_id = validate_episode_id(
        episode_id
    )
    path = (
        dataset_root()
        / "episodes"
        / f"{safe_id}.json"
    )
    episode = _read_json(path)

    if episode.get("episodeId") != safe_id:
        raise EvaluationDataError(
            f"Episode ID mismatch in {path.name}."
        )

    return episode


def load_answer_key() -> dict[str, Any]:
    # Never call this before the model output has been saved.
    return _read_json(
        dataset_root()
        / "answer_key.json"
    )


def save_run(
    run_id: str,
    payload: dict[str, Any],
) -> Path:
    if not _RUN_ID_RE.fullmatch(run_id):
        raise EvaluationDataError(
            "Invalid evaluation run ID."
        )

    target = (
        results_root()
        / f"{run_id}.json"
    )
    temporary = target.with_suffix(
        ".json.tmp"
    )

    serialized = json.dumps(
        payload,
        indent=2,
        ensure_ascii=False,
    )

    temporary.write_text(
        serialized,
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def load_run(
    run_id: str,
) -> dict[str, Any]:
    if not _RUN_ID_RE.fullmatch(run_id):
        raise EvaluationDataError(
            "Invalid evaluation run ID."
        )

    return _read_json(
        results_root()
        / f"{run_id}.json"
    )


def list_runs() -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []

    for path in sorted(
        results_root().glob(
            "eval-run-*.json"
        ),
        reverse=True,
    ):
        try:
            run = _read_json(path)
        except EvaluationDataError:
            continue

        runs.append(
            {
                "runId": run.get(
                    "runId",
                    path.stem,
                ),
                "episodeId": run.get(
                    "episodeId"
                ),
                "status": run.get(
                    "status"
                ),
                "createdAt": run.get(
                    "createdAt"
                ),
                "model": (
                    run.get("model", {})
                    .get("name")
                ),
                "total": (
                    run.get("score", {})
                    .get("total")
                ),
                "overallPass": (
                    run.get("score", {})
                    .get("overallPass")
                ),
                "safetyPass": (
                    run.get("score", {})
                    .get("safetyPass")
                ),
            }
        )

    return runs
