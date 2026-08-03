from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.config import settings
from app.evaluation_injection.cardinal_bridge import (
    _load_complete_episode_pack,
    _phase7_evidence_for_scope,
)
from app.evaluation_injection.episode_pack_scope import build_episode_pack_only_evidence
from app.evaluation_injection.evidence_normalizer import rebuild_from_saved_input
from app.evaluation_injection.grounded_cardinal_client import build_strict_messages, message_fingerprint
from app.evaluation_injection.grounded_prompt_builder import build_grounded_messages

SCENARIOS = (
    "VFIB-STEMI-001", "TORSADES-LQT-002", "VT-ISCHEMIC-003",
    "AFIB-RVR-SEPSIS-004", "CHB-HYPERK-005", "BRADY-DIGTOX-006",
    "SVT-PSVT-007", "NSVT-ECTOPY-008",
)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _episode_root() -> Path:
    return Path(settings.EPISODE_STORAGE_PATH).resolve()


def _select_latest_by_scenario() -> dict[str, Path]:
    latest: dict[str, tuple[float, Path]] = {}
    root = _episode_root()
    for path in root.glob("*/grounded_model_input.json"):
        try:
            payload = _read(path)
        except (OSError, json.JSONDecodeError):
            continue
        scenario = str(payload.get("scenarioId") or "")
        if scenario not in SCENARIOS:
            continue
        modified = path.stat().st_mtime
        if scenario not in latest or modified > latest[scenario][0]:
            latest[scenario] = (modified, path.parent)
    return {key: value[1] for key, value in latest.items()}


def _prepare(source: Path) -> dict[str, Any]:
    record = _read(source / "grounded_model_input.json")
    scenario_id = str(record.get("scenarioId") or "")
    episode_id = str(record.get("episodeId") or source.name)
    incident_id = str(record.get("incidentId") or "")
    stored = record.get("evidenceBundle") or record.get("suppliedEvidence") or {}
    if stored.get("schemaVersion") == "slm-evidence-envelope-v4" and stored.get("clinicalPromptMode") == "episode_pack_only":
        evidence = stored
    else:
        _, evidence = rebuild_from_saved_input(record)
        evidence["completeEpisodePack"] = _load_complete_episode_pack(scenario_id)
        evidence = build_episode_pack_only_evidence(
            evidence,
            phase7_evidence=_phase7_evidence_for_scope(incident_id),
        )
    oracle = evidence.get("oracleContext") or {}
    if evidence.get("clinicalPromptMode") != "episode_pack_only" or oracle.get("available") is not False or oracle.get("excludedByPolicy") is not True:
        raise RuntimeError(f"Episode is not episode-pack-only: {source}")
    messages = build_strict_messages(build_grounded_messages(evidence_bundle=evidence))
    joined = json.dumps(messages, ensure_ascii=False).lower()
    forbidden = ("smart, wilma", "oracle lab trends", "oracle vital trends", "oracle medication", "fhir-ehr-code.cerner.com", "access_token", "refresh_token")
    hits = [item for item in forbidden if item in joined]
    if hits:
        raise RuntimeError("Oracle clinical context leaked into prompt: " + ", ".join(hits))
    return {
        "scenarioId": scenario_id,
        "episodeId": episode_id,
        "incidentId": incident_id,
        "sourceDirectory": str(source),
        "clinicalPromptMode": "episode_pack_only",
        "promptFingerprint": message_fingerprint(messages),
        "messages": messages,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export exact episode-pack-only prompts for Colab.")
    parser.add_argument("--episode-id", action="append", default=[])
    parser.add_argument("--all-scenarios", action="store_true")
    parser.add_argument("--output", default="data/colab_model_benchmark/colab_input_batch.json")
    args = parser.parse_args()
    sources = [(_episode_root()/episode_id).resolve() for episode_id in args.episode_id]
    if args.all_scenarios:
        selected = _select_latest_by_scenario()
        missing = [scenario for scenario in SCENARIOS if scenario not in selected]
        if missing:
            raise RuntimeError("Create/save an episode with Phase 6 evidence for: " + ", ".join(missing))
        sources.extend(selected[scenario] for scenario in SCENARIOS)
    if not sources:
        raise ValueError("Use --episode-id or --all-scenarios.")
    items = [_prepare(source) for source in sources]
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({
        "schemaVersion": "kgen-colab-medical-batch-v2",
        "clinicalPromptMode": "episode_pack_only",
        "itemCount": len(items),
        "items": items,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"created": str(destination.resolve()), "itemCount": len(items), "scenarios": [x["scenarioId"] for x in items]}, indent=2))


if __name__ == "__main__":
    main()
