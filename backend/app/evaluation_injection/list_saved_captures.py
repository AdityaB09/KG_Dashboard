from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.config import settings


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def list_saved_captures(scenario_id: str | None = None) -> list[dict[str, Any]]:
    root = Path(settings.EPISODE_STORAGE_PATH)
    if not root.is_absolute():
        root = (Path.cwd() / root).resolve()

    records: list[dict[str, Any]] = []
    if not root.exists():
        return records

    for directory in root.glob("evalflow-*"):
        if not directory.is_dir():
            continue
        metadata = _read_json(directory / "metadata.json")
        saved_scenario = str(
            metadata.get("evaluationScenarioId")
            or ((metadata.get("evaluationScenario") or {}).get("episodeId"))
            or ""
        )
        if scenario_id and saved_scenario != scenario_id:
            continue

        records.append(
            {
                "episodeId": str(metadata.get("id") or directory.name),
                "scenarioId": saved_scenario,
                "display": str(metadata.get("display") or ""),
                "analysisStatus": metadata.get("analysisStatus"),
                "capturedAt": metadata.get("capturedAt") or metadata.get("createdAt"),
                "hasGroundedInput": (directory / "grounded_model_input.json").exists(),
                "directory": str(directory),
                "modifiedTime": directory.stat().st_mtime,
            }
        )

    records.sort(key=lambda item: item["modifiedTime"], reverse=True)
    for record in records:
        record.pop("modifiedTime", None)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List saved evaluation captures that can be reused for model benchmarking."
    )
    parser.add_argument("--scenario-id", default=None)
    parser.add_argument("--latest", action="store_true")
    args = parser.parse_args()

    records = list_saved_captures(args.scenario_id)
    output: Any = records[0] if args.latest and records else records
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
