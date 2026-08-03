from __future__ import annotations

import argparse

from app.evaluation_injection.canonical_episode_repository import (
    create_canonical_episode,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create an immutable canonical evaluation episode from a completed capture."
    )
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--overwrite", action="store_true")
    arguments = parser.parse_args()

    target = create_canonical_episode(
        scenario_id=arguments.scenario_id,
        source_episode_id=arguments.episode_id,
        overwrite=arguments.overwrite,
    )

    print(target)


if __name__ == "__main__":
    main()
