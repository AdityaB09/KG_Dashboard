from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.evaluation.repository import load_answer_key as load_legacy_answer_key


DEFAULT_WEIGHTS = {
    "diagnosisConsistency": 20,
    "primaryEtiology": 35,
    "contextualEvidence": 30,
    "uncertaintyAndMissingData": 10,
    "avoidsDistractors": 5,
}


class AnswerKeyError(RuntimeError):
    pass


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def answer_key_root() -> Path:
    return _backend_root() / "data" / "evaluation_answer_keys"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AnswerKeyError(f"Answer key not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AnswerKeyError(f"Invalid JSON in answer key {path.name}: {exc}") from exc

    if not isinstance(payload, dict):
        raise AnswerKeyError(f"Answer key {path.name} must contain a JSON object.")

    return payload


def _slug(value: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", value)

    if not words:
        return "concept"

    first, *rest = words
    return first.lower() + "".join(word[:1].upper() + word[1:].lower() for word in rest)


def _legacy_to_universal(
    scenario_id: str,
    legacy_entry: dict[str, Any],
) -> dict[str, Any]:
    synonym_groups: dict[str, list[str]] = {}

    primary_etiology = str(legacy_entry.get("primaryEtiology") or "").strip()
    mechanism = str(legacy_entry.get("mechanism") or "").strip()

    etiology_ids: list[str] = []
    for value in (primary_etiology, mechanism):
        if not value:
            continue
        identifier = _slug(value)
        etiology_ids.append(identifier)
        synonym_groups[identifier] = [value]

    context_ids: list[str] = []
    for item in legacy_entry.get("mustIdentify") or []:
        text = str(item).strip()
        if not text:
            continue
        identifier = _slug(text)
        context_ids.append(identifier)
        synonym_groups[identifier] = [text]

    contributing_ids: list[str] = []
    for item in legacy_entry.get("contributing") or []:
        text = str(item).strip()
        if not text:
            continue
        identifier = _slug(text)
        contributing_ids.append(identifier)
        synonym_groups[identifier] = [text]

    distractor_ids: list[str] = []
    for item in legacy_entry.get("distractors") or []:
        text = str(item).strip()
        if not text:
            continue
        identifier = _slug(text)
        distractor_ids.append(identifier)
        synonym_groups[identifier] = [text]

    return {
        "schemaVersion": "etiology-context-answer-key-v1",
        "scenarioId": scenario_id,
        "authoritativeDiagnosis": {},
        "expectedEtiologyConcepts": etiology_ids,
        "expectedContextConcepts": context_ids,
        "expectedContributingFactors": contributing_ids,
        "acceptableUncertaintyConcepts": [],
        "forbiddenClaims": [],
        "distractorAssertions": distractor_ids,
        "synonymGroups": synonym_groups,
        "uncertaintyPolicy": {
            "emptyAllowed": True,
            "partialCreditForEmpty": True,
        },
        "scoringWeights": DEFAULT_WEIGHTS,
        "source": "legacy_answer_key_runtime_adapter",
    }


def validate_answer_key(payload: dict[str, Any], scenario_id: str | None = None) -> dict[str, Any]:
    required = (
        "schemaVersion",
        "scenarioId",
        "expectedEtiologyConcepts",
        "expectedContextConcepts",
        "expectedContributingFactors",
        "acceptableUncertaintyConcepts",
        "forbiddenClaims",
        "distractorAssertions",
        "synonymGroups",
        "scoringWeights",
    )

    missing = [key for key in required if key not in payload]
    if missing:
        raise AnswerKeyError(
            f"Answer key {payload.get('scenarioId') or scenario_id or '<unknown>'} "
            f"is missing required fields: {missing}"
        )

    if scenario_id and payload.get("scenarioId") != scenario_id:
        raise AnswerKeyError(
            f"Answer key scenario mismatch: expected {scenario_id}, "
            f"found {payload.get('scenarioId')}"
        )

    weights = payload.get("scoringWeights") or {}
    merged_weights = {**DEFAULT_WEIGHTS, **weights}

    if sum(int(value) for value in merged_weights.values()) != 100:
        raise AnswerKeyError(
            f"Scoring weights for {payload.get('scenarioId')} must total 100."
        )

    payload = dict(payload)
    payload["scoringWeights"] = merged_weights
    payload.setdefault("uncertaintyPolicy", {"emptyAllowed": True, "partialCreditForEmpty": True})

    synonym_groups = payload.get("synonymGroups") or {}
    if not isinstance(synonym_groups, dict):
        raise AnswerKeyError("synonymGroups must be a JSON object.")

    for field in (
        "expectedEtiologyConcepts",
        "expectedContextConcepts",
        "expectedContributingFactors",
        "acceptableUncertaintyConcepts",
        "forbiddenClaims",
        "distractorAssertions",
    ):
        values = payload.get(field)
        if not isinstance(values, list):
            raise AnswerKeyError(f"{field} must be an array.")

        for concept_id in values:
            if concept_id not in synonym_groups:
                raise AnswerKeyError(
                    f"Concept '{concept_id}' from {field} has no synonym group."
                )

    return payload


def load_scenario_answer_key(
    scenario_id: str,
    *,
    allow_legacy_fallback: bool = True,
) -> dict[str, Any]:
    path = answer_key_root() / f"{scenario_id}.json"

    if path.exists():
        return validate_answer_key(_read_json(path), scenario_id)

    if not allow_legacy_fallback:
        raise AnswerKeyError(f"Scenario answer key not found: {path}")

    legacy = load_legacy_answer_key()
    entry = (legacy.get("episodes") or {}).get(scenario_id)

    if not isinstance(entry, dict):
        raise AnswerKeyError(
            f"No universal or legacy answer key exists for {scenario_id}."
        )

    return validate_answer_key(_legacy_to_universal(scenario_id, entry), scenario_id)


def list_answer_key_scenarios() -> list[str]:
    root = answer_key_root()
    if not root.exists():
        return []

    return sorted(path.stem for path in root.glob("*.json"))
