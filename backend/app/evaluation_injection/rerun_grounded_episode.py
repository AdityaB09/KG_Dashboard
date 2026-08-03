from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.evaluation_injection.canonical_episode_repository import canonical_episode_dir
from app.evaluation_injection.model_registry import resolve_model
from app.evaluation_injection.universal_evaluation_runner import (
    _default_output_root,
    _episode_dir,
    run_matrix,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run one model against one saved episode or canonical scenario without "
            "injecting the waveform again."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--episode-id")
    source.add_argument("--scenario-id")
    parser.add_argument("--model", required=True)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--run-label", default=None)
    arguments = parser.parse_args()

    model = resolve_model(arguments.model)
    if arguments.run_label:
        model = {**model, "id": arguments.run_label, "displayName": arguments.run_label}

    source_dir = (
        _episode_dir(arguments.episode_id)
        if arguments.episode_id
        else canonical_episode_dir(arguments.scenario_id)
    )
    output_root = Path(arguments.output_root) if arguments.output_root else _default_output_root()

    asyncio.run(
        run_matrix(
            sources=[source_dir],
            models=[model],
            runs=arguments.runs,
            output_root=output_root,
        )
    )


if __name__ == "__main__":
    main()
