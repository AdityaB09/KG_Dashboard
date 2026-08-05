from __future__ import annotations

import argparse
from pathlib import Path
import re

OLD_START = "# BEGIN KGEN V6.0.3 SEMANTIC OVERRIDES"
OLD_END = "# END KGEN V6.0.3 SEMANTIC OVERRIDES"
START = "# BEGIN KGEN V6.0.3.1 VALIDATOR/SCORER AUDIT FIXES"
END = "# END KGEN V6.0.3.1 VALIDATOR/SCORER AUDIT FIXES"

VALIDATOR_REQUIRED = (
    "def _positive_phrase(",
    "def _v4_number_supported(",
    "def _v4_measurement_conflict_errors(",
)
SCORER_REQUIRED = (
    "def _phrase_positive(",
    "def _uncertainty_score(",
)

VALIDATOR_BLOCK = r'''
# BEGIN KGEN V6.0.3.1 VALIDATOR/SCORER AUDIT FIXES
# Final definitions intentionally override earlier implementations. Python
# resolves these names at call time, so existing callers use the corrected
# semantics without creating a second validator implementation.


def _kgen_v6031_phrase_pattern(phrase: str):
    normalized = _normalize(phrase)
    if not normalized:
        return None
    tokens = normalized.replace("-", " ").split()
    separator = r"(?:[\s-]+)"
    return re.compile(
        r"(?<![a-z0-9])"
        + separator.join(re.escape(token) for token in tokens)
        + r"(?![a-z0-9])"
    )


def _kgen_v6031_locally_negated(normalized_sentence: str, match_start: int) -> bool:
    prefix = normalized_sentence[max(0, match_start - 90):match_start]
    return any(marker in prefix for marker in NEGATION_MARKERS)


def _positive_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    for sentence in _sentences(text):
        normalized = _normalize(sentence)
        for phrase in phrases:
            pattern = _kgen_v6031_phrase_pattern(phrase)
            if pattern is None:
                continue
            for match in pattern.finditer(normalized):
                prefix = normalized[max(0, match.start() - 8):match.start()]
                # Do not match "sustained VT" inside "non-sustained VT".
                if prefix.endswith("non-") or prefix.endswith("non "):
                    continue
                if _kgen_v6031_locally_negated(normalized, match.start()):
                    continue
                if any(marker in normalized for marker in BASELINE_MARKERS):
                    continue
                return True
    return False


def _supported_numbers(evidence: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for match in re.finditer(r"(?<![\w.])[-+]?\d+(?:\.\d+)?", _flatten(evidence)):
        try:
            values.append(float(match.group(0)))
        except ValueError:
            continue
    return values


def _kgen_v6031_numeric_equivalent(output_value: float, evidence_value: float) -> bool:
    difference = abs(float(output_value) - float(evidence_value))
    # Unit-aware checks happen elsewhere; this tolerance handles safe display
    # rounding such as 54 bpm from 54.098 bpm without accepting large changes.
    if difference <= max(0.25, abs(float(evidence_value)) * 0.015):
        return True
    if difference < 1.0 and round(float(output_value)) == round(float(evidence_value)):
        return True
    return False


def _unsupported_numbers(text: str, evidence: dict[str, Any]) -> list[str]:
    supported = _supported_numbers(evidence)
    output: list[str] = []
    for match in re.finditer(r"(?<![\w.])[-+]?\d+(?:\.\d+)?", text):
        token = match.group(0)
        number = float(token)
        if abs(number) < 4:
            continue
        if any(_kgen_v6031_numeric_equivalent(number, item) for item in supported):
            continue
        if token not in output:
            output.append(token)
    return output[:12]


def _v4_number_supported(*, number: float, text: str, start: int, evidence: dict[str, Any]):
    supported = _v4_numeric_values(evidence)
    for item in supported:
        if _kgen_v6031_numeric_equivalent(number, item):
            return True, None
    if not _v4_flag("SLM_NUMERIC_TOLERANCE_ENABLED", False):
        return False, None
    window = _normalize(text[max(0, start - 35): start + 35])
    if any(marker in window for marker in ("about", "approximately", "around", "roughly", "nearly", "~")):
        for item in supported:
            if abs(number - item) <= max(1.0, abs(item) * 0.06):
                return True, f"Safe approximate numeric paraphrase: {number:g}."
    return False, None


def _kgen_v6031_conflict_is_etiology_material(conflict: dict[str, Any]) -> bool:
    return any(
        conflict.get(key) is True
        for key in (
            "etiologyMaterial",
            "materialToEtiology",
            "clinicallyMaterialToEtiology",
            "presentationMaterial",
        )
    )


def _v4_measurement_conflict_errors(facts: str, evidence: dict[str, Any]) -> list[str]:
    output: list[str] = []
    normalized = _normalize(facts)
    for conflict in evidence.get("measurementConflicts") or []:
        if not isinstance(conflict, dict):
            continue
        if not _kgen_v6031_conflict_is_etiology_material(conflict):
            # Technical/non-etiologic conflicts remain audit metadata and are
            # not required in the user-facing clinical response.
            continue
        controlled = str(
            conflict.get("controlledEventValue", conflict.get("controlledValue", ""))
        )
        independent = str(
            conflict.get("phase6EventValue", conflict.get("independentValue", ""))
        )
        acknowledged = (
            any(
                term in normalized
                for term in ("differs", "difference", "discrepancy", "conflict", "does not match")
            )
            and (controlled in facts or "controlled" in normalized or "episode" in normalized)
            and (independent in facts or "independent" in normalized or "phase 6" in normalized)
        )
        if not acknowledged:
            output.append(
                "Etiologically material measurement conflict was not acknowledged: "
                f"{conflict.get('id')}."
            )
    return output
# END KGEN V6.0.3.1 VALIDATOR/SCORER AUDIT FIXES
'''

SCORER_BLOCK = r'''
# BEGIN KGEN V6.0.3.1 VALIDATOR/SCORER AUDIT FIXES


def _kgen_v6031_phrase_pattern(phrase: str):
    normalized = _normalize(phrase)
    if not normalized:
        return None
    tokens = normalized.replace("-", " ").split()
    separator = r"(?:[\s-]+)"
    return re.compile(
        r"(?<![a-z0-9])"
        + separator.join(re.escape(token) for token in tokens)
        + r"(?![a-z0-9])"
    )


def _phrase_positive(text: str, phrase: str) -> bool:
    pattern = _kgen_v6031_phrase_pattern(phrase)
    if pattern is None:
        return False
    for sentence in _sentences(text):
        normalized = _normalize(sentence)
        for match in pattern.finditer(normalized):
            prefix = normalized[max(0, match.start() - 90):match.start()]
            if prefix.endswith("non-") or prefix.endswith("non "):
                continue
            if any(marker in prefix for marker in NEGATION_MARKERS):
                continue
            return True
    return False


def _uncertainty_score(*, answer_key: dict[str, Any], response: dict[str, Any], maximum: int):
    expected = list(answer_key.get("acceptableUncertaintyConcepts") or [])
    actual_items = response.get("uncertaintyAndMissingData") or []
    actual = _flatten(actual_items)
    policy = answer_key.get("uncertaintyPolicy") or {}

    # Apply the documented empty-response policy before concept matching.
    # This is required even when the answer key lists acceptable uncertainty
    # concepts. An empty array can still be valid when the leading etiology is
    # adequately supported.
    if not actual_items and policy.get("emptyAllowed", True):
        score = (
            round(maximum * 0.5)
            if policy.get("partialCreditForEmpty", True)
            else maximum
        )
        return score, [], [], expected, {}

    if expected:
        return _score_concepts(
            answer_key=answer_key,
            concept_ids=expected,
            actual=actual,
            maximum=maximum,
        )

    if actual_items:
        meaningful = any(
            marker in _normalize(actual)
            for marker in (
                "uncertain",
                "unknown",
                "not established",
                "does not establish",
                "cannot determine",
                "not available",
                "cannot exclude",
                "cannot distinguish",
            )
        )
        score = maximum if meaningful else round(maximum * 0.5)
        return score, [], [], [], {}

    return 0, [], [], [], {}
# END KGEN V6.0.3.1 VALIDATOR/SCORER AUDIT FIXES
'''

CLI_SOURCE = r'''from __future__ import annotations

import argparse
import csv
import json
import tempfile
import zipfile
import sys
import types
from collections import defaultdict
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def extract(zip_path: Path, target: Path) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(target)


def find_results(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    output: list[tuple[Path, dict[str, Any]]] = []
    for path in root.rglob("*.json"):
        try:
            payload = load(path)
        except Exception:
            continue
        if (
            payload.get("scenarioId")
            and (payload.get("modelResponse") or payload.get("parsedResponse"))
        ):
            output.append((path, payload))
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-root", required=True)
    parser.add_argument("--input-zip", required=True)
    parser.add_argument("--results-zip", required=True)
    parser.add_argument("--output-directory", required=True)
    args = parser.parse_args()

    backend_root = Path(args.backend_root).resolve()
    if not (backend_root / "app").is_dir():
        raise FileNotFoundError(
            f"Backend root must contain app/: {backend_root}"
        )

    sys.path.insert(0, str(backend_root))

    # Avoid importing app.evaluation_injection.__init__, which eagerly loads
    # the full waveform service stack. The validator/scorer CLI needs only
    # the source modules below and should run with validator-only dependencies.
    import app
    package_name = "app.evaluation_injection"
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(backend_root / "app" / "evaluation_injection")]
        package.__package__ = package_name
        sys.modules[package_name] = package

    from app.evaluation_injection.answer_key_loader import load_scenario_answer_key
    from app.evaluation_injection.compatibility_adapter import (
        adapt_v6_0_3_to_legacy_validator,
    )
    from app.evaluation_injection.etiology_context_scorer import (
        score_etiology_context_response,
    )
    from app.evaluation_injection.response_validator import validate_grounded_response

    output_root = Path(args.output_directory).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as input_temp, tempfile.TemporaryDirectory() as result_temp:
        input_root = Path(input_temp)
        result_root = Path(result_temp)
        extract(Path(args.input_zip).resolve(), input_root)
        extract(Path(args.results_zip).resolve(), result_root)

        manifest = load(input_root / "manifest.json")
        metadata = {
            item["scenarioId"]: item
            for item in manifest.get("items") or []
            if isinstance(item, dict) and item.get("scenarioId")
        }

        for path, result in find_results(result_root):
            scenario = str(result["scenarioId"])
            item = metadata.get(scenario)
            if not item:
                continue

            expected_fingerprint = str(item.get("promptFingerprint") or "")
            result_fingerprint = str(result.get("promptFingerprint") or "")
            if expected_fingerprint and result_fingerprint != expected_fingerprint:
                raise RuntimeError(
                    f"Prompt fingerprint mismatch for {scenario}: "
                    f"{result_fingerprint!r} != {expected_fingerprint!r}"
                )

            model_response = result.get("modelResponse") or result.get("parsedResponse")
            legacy = adapt_v6_0_3_to_legacy_validator(model_response)

            item_root = input_root / "items" / scenario
            diagnostic = load(item_root / "diagnostic_event.json")
            evidence = load(item_root / "validator_evidence.json")

            validation = validate_grounded_response(
                response=legacy,
                diagnostic_event=diagnostic,
                supplied_evidence=evidence,
            )

            # Mandatory real scenario answer key. Never use an empty fallback.
            answer_key = load_scenario_answer_key(
                scenario,
                allow_legacy_fallback=False,
            )

            score = score_etiology_context_response(
                episode_id=scenario,
                model_response=legacy,
                diagnostic_event=diagnostic,
                validation=validation,
                answer_key=answer_key,
            )

            row = {
                "model": result.get("model"),
                "scenarioId": scenario,
                "validContract": bool(result.get("validContract")),
                "generationAttempts": result.get("generationAttempts"),
                "contractNormalized": result.get("contractNormalized"),
                "latencySeconds": result.get("latencySeconds"),
                "inputTokens": result.get("inputTokens"),
                "outputTokens": result.get("outputTokens"),
                "peakGpuMemoryGiB": result.get("peakGpuMemoryGiB"),
                "accepted": bool(validation.get("accepted")),
                "validatorPassed": bool(
                    validation.get("validatorPassed", validation.get("accepted"))
                ),
                "displayableWithReview": bool(validation.get("displayableWithReview")),
                "hardErrorCount": len(validation.get("hardErrors") or []),
                "qualityErrorCount": len(validation.get("qualityErrors") or []),
                "unsupportedFactCount": len(validation.get("unsupportedFacts") or []),
                "contradictionCount": len(validation.get("contradictions") or []),
                "safetyPass": bool(score.get("safetyPass")),
                "overallPass": bool(score.get("overallPass")),
                "total": score.get("total"),
                "grade": score.get("grade"),
                "diagnosisConsistency": score.get("diagnosisConsistency"),
                "primaryEtiology": score.get("primaryEtiology"),
                "contextualEvidence": score.get("contextualEvidence"),
                "uncertainty": score.get("uncertaintyAndMissingData"),
                "avoidsDistractors": score.get("avoidsDistractors"),
                "answerKeySource": answer_key.get("source"),
                "file": str(path),
            }
            rows.append(row)
            details.append(
                {
                    **row,
                    "validation": validation,
                    "score": score,
                    "modelResponse": model_response,
                    "legacyResponse": legacy,
                    "answerKey": answer_key,
                }
            )

    rows.sort(key=lambda item: (str(item.get("model")), str(item.get("scenarioId"))))
    details.sort(key=lambda item: (str(item.get("model")), str(item.get("scenarioId"))))

    (output_root / "local_validation_report.json").write_text(
        json.dumps(details, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    with (output_root / "local_validation_report.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        fieldnames = list(rows[0]) if rows else ["model", "scenarioId"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("model"))].append(row)

    scorecard: list[dict[str, Any]] = []
    for model, model_rows in sorted(grouped.items()):
        scorecard.append(
            {
                "model": model,
                "scenarioCount": len(model_rows),
                "acceptedCount": sum(bool(item["accepted"]) for item in model_rows),
                "validatorPassedCount": sum(
                    bool(item["validatorPassed"]) for item in model_rows
                ),
                "averageScore": round(
                    sum(float(item.get("total") or 0) for item in model_rows)
                    / len(model_rows),
                    2,
                ),
            }
        )

    (output_root / "model_score_details.json").write_text(
        json.dumps(details, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    with (output_root / "model_scorecard.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        fieldnames = list(scorecard[0]) if scorecard else ["model"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(scorecard)

    audit = ["# KGEN V6.0.3.1 scenario audit", ""]
    audit.extend(
        f"- {row['model']} | {row['scenarioId']} | "
        f"accepted={row['accepted']} | score={row['total']} | grade={row['grade']}"
        for row in rows
    )
    (output_root / "scenario_audit.md").write_text(
        "\n".join(audit) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "outputDirectory": str(output_root),
                "resultCount": len(rows),
                "modelCount": len(scorecard),
                "answerKeysLoaded": len(rows),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
'''

TEST_SOURCE = r'''from __future__ import annotations

import sys
import types
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT))

import app

PACKAGE_NAME = "app.evaluation_injection"
if PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(BACKEND_ROOT / "app" / "evaluation_injection")]
    package.__package__ = PACKAGE_NAME
    sys.modules[PACKAGE_NAME] = package

from app.evaluation_injection import etiology_context_scorer as scorer
from app.evaluation_injection import response_validator as validator


def test_regular_does_not_match_irregular() -> None:
    assert not validator._positive_phrase("The rhythm was irregular.", ("regular",))
    assert not scorer._phrase_positive("The rhythm was irregular.", "regular")


def test_sustained_does_not_match_non_sustained() -> None:
    text = "The patient had non-sustained ventricular tachycardia."
    assert not validator._positive_phrase(
        text,
        ("sustained ventricular tachycardia",),
    )
    assert not scorer._phrase_positive(
        text,
        "sustained ventricular tachycardia",
    )


def test_safe_rounding_is_supported() -> None:
    assert validator._kgen_v6031_numeric_equivalent(54.0, 54.098)


def test_empty_uncertainty_gets_partial_credit_without_expected_concepts() -> None:
    score, *_ = scorer._uncertainty_score(
        answer_key={
            "acceptableUncertaintyConcepts": [],
            "uncertaintyPolicy": {
                "emptyAllowed": True,
                "partialCreditForEmpty": True,
            },
        },
        response={"uncertaintyAndMissingData": []},
        maximum=10,
    )
    assert score == 5


def test_empty_uncertainty_gets_partial_credit_with_expected_concepts() -> None:
    score, matched, partial, missing, strengths = scorer._uncertainty_score(
        answer_key={
            "acceptableUncertaintyConcepts": ["mechanismUnknown"],
            "uncertaintyPolicy": {
                "emptyAllowed": True,
                "partialCreditForEmpty": True,
            },
            "synonymGroups": {
                "mechanismUnknown": ["mechanism cannot be distinguished"],
            },
        },
        response={"uncertaintyAndMissingData": []},
        maximum=10,
    )
    assert score == 5
    assert matched == []
    assert partial == []
    assert missing == ["mechanismUnknown"]
    assert strengths == {}


def test_non_etiologic_technical_conflict_not_required() -> None:
    evidence = {
        "measurementConflicts": [
            {
                "id": "qrs",
                "material": True,
                "controlledValue": 88,
                "independentValue": 54.098,
            }
        ]
    }
    assert validator._v4_measurement_conflict_errors(
        "No conflict statement is needed.",
        evidence,
    ) == []


def test_etiologic_conflict_is_required() -> None:
    evidence = {
        "measurementConflicts": [
            {
                "id": "qrs",
                "etiologyMaterial": True,
                "controlledValue": 88,
                "independentValue": 54.098,
            }
        ]
    }
    assert validator._v4_measurement_conflict_errors(
        "No comparison was provided.",
        evidence,
    )
'''


def remove_marked_block(text: str, start: str, end: str) -> str:
    if start not in text or end not in text:
        return text
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    return pattern.sub("", text).rstrip() + "\n"


def replace_block(text: str, block: str) -> str:
    text = remove_marked_block(text, OLD_START, OLD_END)
    text = remove_marked_block(text, START, END)
    return text.rstrip() + "\n\n" + block.strip() + "\n"


def patch_module(path: Path, required: tuple[str, ...], block: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    text = path.read_text(encoding="utf-8")
    missing = [marker for marker in required if marker not in text]
    if missing:
        raise RuntimeError(
            f"Unsafe source shape for {path}; missing expected owners: {missing}"
        )
    path.write_text(replace_block(text, block), encoding="utf-8")


def resolve_backend(project_root: Path) -> Path:
    if (project_root / "backend" / "app").is_dir():
        return project_root / "backend"
    if (project_root / "app").is_dir():
        return project_root
    raise FileNotFoundError(
        "Could not locate backend/app. Pass the project root or backend root."
    )


def candidate_cli_paths(project_root: Path, backend: Path) -> list[Path]:
    candidates = [
        project_root
        / "kgen_prompt_refinement_v6_0_3"
        / "local_validator_scorer"
        / "cli"
        / "validate_and_score_results_v6_0_3.py",
        project_root
        / "KGEN_V6_0_3_WORKFLOW"
        / "local_validator_scorer"
        / "cli"
        / "validate_and_score_results_v6_0_3.py",
        backend
        / "KGEN_V6_0_3_WORKFLOW"
        / "local_validator_scorer"
        / "cli"
        / "validate_and_score_results_v6_0_3.py",
    ]
    return list(dict.fromkeys(path.resolve() for path in candidates if path.is_file()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    backend = resolve_backend(project_root)

    patch_module(
        backend / "app/evaluation_injection/response_validator.py",
        VALIDATOR_REQUIRED,
        VALIDATOR_BLOCK,
    )
    patch_module(
        backend / "app/evaluation_injection/etiology_context_scorer.py",
        SCORER_REQUIRED,
        SCORER_BLOCK,
    )

    cli_paths = candidate_cli_paths(project_root, backend)
    if not cli_paths:
        raise FileNotFoundError(
            "No V6.0.3 local validator/scorer CLI was found. Expected it under "
            "kgen_prompt_refinement_v6_0_3/local_validator_scorer/cli or "
            "KGEN_V6_0_3_WORKFLOW/local_validator_scorer/cli."
        )
    for path in cli_paths:
        path.write_text(CLI_SOURCE, encoding="utf-8")

    test_path = (
        backend
        / "tests/evaluation_injection/test_validator_scorer_semantics_v6_0_3.py"
    )
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(TEST_SOURCE, encoding="utf-8")

    print("Applied KGEN V6.0.3.1 validator/scorer audit fixes.")
    print(f"Backend: {backend}")
    print("Patched CLI copies:")
    for path in cli_paths:
        print(f"- {path}")
    print(f"Updated tests: {test_path}")


if __name__ == "__main__":
    main()
