from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a completed API Range + "
            "Episode evaluation."
        )
    )
    parser.add_argument(
        "--episode-dir",
        required=True,
    )
    args = parser.parse_args()

    episode_dir = (
        Path(args.episode_dir)
        .expanduser()
        .resolve()
    )

    metadata = json.loads(
        (
            episode_dir
            / "metadata.json"
        ).read_text(
            encoding="utf-8"
        )
    )

    oracle_demo = (
        metadata.get("oracleDemo")
        or {}
    )
    api_capture = (
        metadata.get(
            "apiRangeCapture"
        )
        or {}
    )
    segments = (
        metadata.get(
            "sourceSegments"
        )
        or []
    )

    checks = {
        "apiRangeEpisodeCapture": (
            metadata.get(
                "captureSource"
            )
            == "hybrid_api_range_injection"
        ),
        "apiRangeBaseWaveform": (
            metadata.get(
                "baseWaveformSource"
            )
            == "api-range"
        ),
        "oracleMappedScenario": bool(
            oracle_demo.get(
                "scenarioId"
            )
        ),
        "episodePackOnly": (
            oracle_demo.get(
                "clinicalContextMode"
            )
            == "episode_pack_only"
        ),
        "oracleFhirClinicalContextExcluded": (
            oracle_demo.get(
                "oracleFhirContextUsed"
            )
            is False
        ),
        "correctSegmentOrder": (
            [
                item.get("type")
                for item in segments
            ]
            == [
                "pre_event",
                "controlled_event",
                "post_event",
            ]
        ),
        "captureModeRecorded": (
            api_capture.get(
                "captureMode"
            )
            in {
                "repeat_snapshot",
                "continuous",
            }
        ),
        "sourceReplayRecorded": (
            api_capture.get(
                "snapshotReplayed"
            )
            is not None
        ),
    }

    passed = all(
        checks.values()
    )

    print(
        json.dumps(
            {
                "status": (
                    "PASS"
                    if passed
                    else "FAIL"
                ),
                "episodeId": (
                    metadata.get("id")
                ),
                "scenarioId": (
                    oracle_demo.get(
                        "scenarioId"
                    )
                ),
                "episodePackPatient": (
                    oracle_demo.get(
                        "episodePackPatient"
                    )
                ),
                "apiRangeCapture": (
                    api_capture
                ),
                "checks": checks,
            },
            indent=2,
        )
    )

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
