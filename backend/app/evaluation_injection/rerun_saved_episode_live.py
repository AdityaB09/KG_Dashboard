from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.config import settings
from app.evaluation_injection.cardinal_bridge import (
    rerun_grounded_from_saved_input,
)


def _episode_dir(episode_id: str) -> Path:
    return Path(settings.EPISODE_STORAGE_PATH) / episode_id


async def _run(*, episode_id: str, model: str) -> None:
    episode_dir = _episode_dir(episode_id)
    if not episode_dir.exists():
        raise FileNotFoundError(f"Episode directory not found: {episode_dir}")

    result = await rerun_grounded_from_saved_input(
        episode_dir=episode_dir,
        model_override=model,
        artifact_dir=episode_dir,
        update_phase7_storage=True,
    )

    print(
        json.dumps(
            {
                "episodeId": episode_id,
                "model": result.get("model"),
                "status": result.get("status"),
                "validation": result.get("validation"),
                "score": result.get("score"),
                "responseFile": result.get("responseFile"),
                "phase7Updated": bool(result.get("phase7SlmResponse")),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Rerun one already captured evaluation episode with an exact Ollama "
            "model and update the live Phase 7 widget artifacts."
        )
    )
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--model", required=True)
    arguments = parser.parse_args()

    asyncio.run(_run(episode_id=arguments.episode_id, model=arguments.model))


if __name__ == "__main__":
    main()
