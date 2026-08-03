from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from app.evaluation.repository import load_answer_key
from app.evaluation_injection.answer_key_loader import (
    DEFAULT_WEIGHTS,
    answer_key_root,
)


def _slug(value: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", value)
    if not words:
        return "concept"
    first, *rest = words
    return first.lower() + "".join(word[:1].upper() + word[1:].lower() for word in rest)


def _add_group(groups: dict[str, list[str]], value: str) -> str:
    identifier = _slug(value)
    suffix = 2
    base = identifier

    while identifier in groups and value not in groups[identifier]:
        identifier = f"{base}{suffix}"
        suffix += 1

    groups.setdefault(identifier, [])
    if value not in groups[identifier]:
        groups[identifier].append(value)
    return identifier


def convert_entry(scenario_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    groups: dict[str, list[str]] = {}

    etiology = [
        _add_group(groups, str(value))
        for value in (entry.get("primaryEtiology"), entry.get("mechanism"))
        if str(value or "").strip()
    ]
    context = [
        _add_group(groups, str(value))
        for value in entry.get("mustIdentify") or []
        if str(value).strip()
    ]
    contributors = [
        _add_group(groups, str(value))
        for value in entry.get("contributing") or []
        if str(value).strip()
    ]
    distractors = [
        _add_group(groups, str(value))
        for value in entry.get("distractors") or []
        if str(value).strip()
    ]

    return {
        "schemaVersion": "etiology-context-answer-key-v1",
        "scenarioId": scenario_id,
        "authoritativeDiagnosis": {},
        "expectedEtiologyConcepts": etiology,
        "expectedContextConcepts": context,
        "expectedContributingFactors": contributors,
        "acceptableUncertaintyConcepts": [],
        "forbiddenClaims": [],
        "distractorAssertions": distractors,
        "synonymGroups": groups,
        "uncertaintyPolicy": {
            "emptyAllowed": True,
            "partialCreditForEmpty": True,
        },
        "scoringWeights": DEFAULT_WEIGHTS,
        "source": "legacy_answer_key_migration",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert the existing combined answer_key.json into one file per scenario."
    )
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--overwrite", action="store_true")
    arguments = parser.parse_args()

    output_root = Path(arguments.output_root) if arguments.output_root else answer_key_root()
    output_root.mkdir(parents=True, exist_ok=True)

    legacy = load_answer_key()
    episodes = legacy.get("episodes") or {}

    for scenario_id, entry in episodes.items():
        if not isinstance(entry, dict):
            continue

        target = output_root / f"{scenario_id}.json"
        if target.exists() and not arguments.overwrite:
            print(f"skip {target}")
            continue

        target.write_text(
            json.dumps(convert_entry(scenario_id, entry), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"wrote {target}")


if __name__ == "__main__":
    main()
