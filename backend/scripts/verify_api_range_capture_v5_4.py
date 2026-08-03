from __future__ import annotations

import argparse
import json
from pathlib import Path


API_RANGE_SOURCES = {
    "api-range",
    "api_range",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify that an evaluation episode used API Range "
            "for pre-capture and post-capture."
        )
    )
    parser.add_argument("--episode-dir", required=True)
    arguments = parser.parse_args()

    episode_dir = Path(arguments.episode_dir).expanduser().resolve()
    metadata_path = episode_dir / "metadata.json"

    if not metadata_path.exists():
        raise FileNotFoundError(
            f"metadata.json was not found: {metadata_path}"
        )

    metadata = json.loads(
        metadata_path.read_text(encoding="utf-8")
    )

    source = str(
        metadata.get("baseWaveformSource") or ""
    ).strip().lower()

    segments = metadata.get("sourceSegments") or []
    api_range = metadata.get("apiRangeCapture") or {}
    completeness = metadata.get("captureCompleteness") or {}

    segment_types = [
        str(item.get("type") or "")
        for item in segments
        if isinstance(item, dict)
    ]

    checks = {
        "evaluationEpisode": (
            metadata.get("mode") == "evaluation_injection"
        ),
        "apiRangeCaptureSource": (
            metadata.get("captureSource")
            == "hybrid_api_range_injection"
        ),
        "apiRangeBaseWaveform": (
            source in API_RANGE_SOURCES
        ),
        "segmentOrder": (
            segment_types
            == [
                "pre_event",
                "controlled_event",
                "post_event",
            ]
        ),
        "apiRangeMetadataPresent": bool(api_range),
        "apiRangeDidNotWrap": (
            api_range.get("wrappedDuringEvaluation") is False
        ),
        "preCaptureComplete": bool(
            completeness.get("preContextComplete")
        ),
        "postCaptureComplete": bool(
            completeness.get("postContextComplete")
        ),
        "completeCapture": bool(
            completeness.get("captureComplete")
        ),
    }

    passed = all(checks.values())

    result = {
        "status": "PASS" if passed else "FAIL",
        "episodeId": metadata.get("id"),
        "captureSource": metadata.get("captureSource"),
        "baseWaveformSource": metadata.get("baseWaveformSource"),
        "sourceSegments": segments,
        "apiRangeCapture": api_range,
        "checks": checks,
    }

    print(json.dumps(result, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
