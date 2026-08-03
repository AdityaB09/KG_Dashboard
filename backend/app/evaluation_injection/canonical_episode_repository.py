from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings


CANONICAL_FILES = (
    "metadata.json",
    "analysis.json",
    "clinical_context.json",
    "grounded_model_input.json",
    "diagnostic_event.json",
    "waveforms.npz",
    "capture_metadata.json",
)


class CanonicalEpisodeError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def episode_root() -> Path:
    return Path(settings.EPISODE_STORAGE_PATH)


def canonical_root() -> Path:
    return episode_root().parent / "canonical_evaluation_episodes"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def create_canonical_episode(
    *,
    scenario_id: str,
    source_episode_id: str,
    overwrite: bool = False,
) -> Path:
    source = episode_root() / source_episode_id
    target = canonical_root() / scenario_id

    if not source.exists():
        raise CanonicalEpisodeError(f"Source episode directory not found: {source}")

    grounded_input = source / "grounded_model_input.json"
    if not grounded_input.exists():
        raise CanonicalEpisodeError(
            "The source episode does not contain grounded_model_input.json. "
            "Run grounded analysis once before canonicalizing it."
        )

    input_record = json.loads(grounded_input.read_text(encoding="utf-8"))
    input_scenario = str(input_record.get("scenarioId") or "")

    if input_scenario and input_scenario != scenario_id:
        raise CanonicalEpisodeError(
            f"Scenario mismatch: requested {scenario_id}, input contains {input_scenario}."
        )

    if target.exists():
        if not overwrite:
            raise CanonicalEpisodeError(
                f"Canonical scenario already exists and is immutable: {target}"
            )
        shutil.rmtree(target)

    target.mkdir(parents=True, exist_ok=False)
    copied: dict[str, dict[str, Any]] = {}

    for filename in CANONICAL_FILES:
        source_file = source / filename
        if not source_file.exists():
            continue

        target_file = target / filename
        shutil.copy2(source_file, target_file)
        copied[filename] = {
            "sizeBytes": target_file.stat().st_size,
            "sha256": _sha256(target_file),
        }

    manifest = {
        "schemaVersion": "canonical-evaluation-episode-v1",
        "scenarioId": scenario_id,
        "sourceEpisodeId": source_episode_id,
        "sourceEpisodeDirectory": str(source),
        "createdAt": _now_iso(),
        "diagnosticEventSchema": (
            (input_record.get("diagnosticEvent") or {}).get("schemaVersion")
        ),
        "evidenceSchema": (
            (input_record.get("evidenceBundle") or {}).get("schemaVersion")
        ),
        "immutable": True,
        "files": copied,
    }
    _atomic_json(target / "manifest.json", manifest)
    return target


def canonical_episode_dir(scenario_id: str) -> Path:
    target = canonical_root() / scenario_id

    if not target.exists():
        raise CanonicalEpisodeError(
            f"No canonical episode exists for {scenario_id}: {target}"
        )

    if not (target / "grounded_model_input.json").exists():
        raise CanonicalEpisodeError(
            f"Canonical episode is incomplete: {target}"
        )

    return target


def list_canonical_scenarios() -> list[str]:
    root = canonical_root()
    if not root.exists():
        return []

    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and (path / "manifest.json").exists()
    )
